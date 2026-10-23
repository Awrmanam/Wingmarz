from __future__ import annotations

from typing import Any

import aiosqlite

import config
from database import db
from group_ops_service import group_ops_service
from kyc_service import kyc_service
from trust_service import trust_service


class OrderKycGate:
    """Payment-first, fulfillment-after-KYC gate for reseller orders."""

    @property
    def db_path(self) -> str:
        return config.DATABASE_PATH

    async def ensure_schema(self) -> None:
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS order_kyc_gate (
                    order_id INTEGER PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    state TEXT NOT NULL DEFAULT 'awaiting_kyc',
                    payment_approved_by INTEGER,
                    payment_approved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    reviewed_by INTEGER,
                    reviewed_at TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE INDEX IF NOT EXISTS idx_order_kyc_gate_user_state
                ON order_kyc_gate(user_id,state);
                """
            )
            await conn.commit()

    async def requires_kyc(self, order: dict[str, Any]) -> bool:
        user_id = int(order.get("user_id") or 0)
        if user_id <= 0:
            return True
        if await trust_service.is_identity_approved(user_id):
            return False

        # Existing panel renewals may explicitly be exempt from KYC.
        if str(order.get("order_type") or "").lower() == "renew":
            target_admin_id = int(order.get("target_admin_id") or 0)
            if target_admin_id and not await kyc_service.is_required(target_admin_id):
                return False
        return True

    async def hold_after_payment(self, order: dict[str, Any], actor_id: int) -> bool:
        order_id = int(order.get("id") or 0)
        user_id = int(order.get("user_id") or 0)
        if not order_id or not user_id:
            return False
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute("BEGIN IMMEDIATE")
            cur = await conn.execute(
                """
                UPDATE orders
                SET status='paid_awaiting_kyc', approved_by=?, updated_at=CURRENT_TIMESTAMP
                WHERE id=? AND status IN ('pending','submitted','failed')
                """,
                (int(actor_id), order_id),
            )
            if cur.rowcount != 1:
                await conn.rollback()
                return False
            await conn.execute(
                """
                INSERT INTO order_kyc_gate(
                    order_id,user_id,state,payment_approved_by,payment_approved_at,updated_at
                ) VALUES(?,?,'awaiting_kyc',?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)
                ON CONFLICT(order_id) DO UPDATE SET
                    state='awaiting_kyc',
                    payment_approved_by=excluded.payment_approved_by,
                    payment_approved_at=CURRENT_TIMESTAMP,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (order_id, user_id, int(actor_id)),
            )
            await conn.commit()

        await group_ops_service.audit(
            actor_id=int(actor_id),
            action="order.payment_approved_awaiting_kyc",
            subject_type="order",
            subject_id=order_id,
            old_value={"status": order.get("status")},
            new_value={"status": "paid_awaiting_kyc", "user_id": user_id},
        )
        return True

    async def has_waiting_order(self, user_id: int) -> bool:
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            async with conn.execute(
                """
                SELECT 1 FROM orders
                WHERE user_id=? AND status='paid_awaiting_kyc'
                LIMIT 1
                """,
                (int(user_id),),
            ) as cur:
                return await cur.fetchone() is not None

    async def release_after_kyc(self, user_id: int, reviewer_id: int) -> list[dict[str, Any]]:
        """Return paid orders to submitted state after identity approval."""
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            await conn.execute("BEGIN IMMEDIATE")
            async with conn.execute(
                """
                SELECT o.* FROM orders o
                JOIN order_kyc_gate g ON g.order_id=o.id
                WHERE g.user_id=? AND g.state='awaiting_kyc'
                  AND o.status='paid_awaiting_kyc'
                ORDER BY o.created_at ASC
                """,
                (int(user_id),),
            ) as cur:
                rows = [dict(row) for row in await cur.fetchall()]
            for order in rows:
                await conn.execute(
                    "UPDATE orders SET status='submitted',updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='paid_awaiting_kyc'",
                    (int(order["id"]),),
                )
                await conn.execute(
                    """
                    UPDATE order_kyc_gate
                    SET state='kyc_approved',reviewed_by=?,reviewed_at=CURRENT_TIMESTAMP,
                        updated_at=CURRENT_TIMESTAMP
                    WHERE order_id=?
                    """,
                    (int(reviewer_id), int(order["id"])),
                )
            await conn.commit()

        for order in rows:
            await group_ops_service.audit(
                actor_id=int(reviewer_id),
                action="order.kyc_gate_released",
                subject_type="order",
                subject_id=int(order["id"]),
                new_value={"status": "submitted", "user_id": int(user_id)},
            )
        return rows

    async def expire_stale(self, hours: int = 48) -> list[dict[str, Any]]:
        """Move unpaid-fulfillment holds to manual refund review after the KYC deadline."""
        await self.ensure_schema()
        hours = max(1, int(hours))
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            await conn.execute("BEGIN IMMEDIATE")
            async with conn.execute(
                """
                SELECT o.* FROM orders o
                JOIN order_kyc_gate g ON g.order_id=o.id
                WHERE g.state='awaiting_kyc'
                  AND o.status='paid_awaiting_kyc'
                  AND g.payment_approved_at <= datetime('now', ?)
                ORDER BY g.payment_approved_at ASC
                """,
                (f"-{hours} hours",),
            ) as cur:
                rows = [dict(row) for row in await cur.fetchall()]
            for order in rows:
                order_id = int(order["id"])
                await conn.execute(
                    """
                    UPDATE orders
                    SET status='refund_pending',updated_at=CURRENT_TIMESTAMP
                    WHERE id=? AND status='paid_awaiting_kyc'
                    """,
                    (order_id,),
                )
                await conn.execute(
                    """
                    UPDATE order_kyc_gate
                    SET state='refund_pending',reviewed_at=CURRENT_TIMESTAMP,
                        updated_at=CURRENT_TIMESTAMP
                    WHERE order_id=? AND state='awaiting_kyc'
                    """,
                    (order_id,),
                )
            await conn.commit()
        return rows

    async def refund_pending_after_rejection(self, user_id: int, reviewer_id: int) -> list[int]:
        """Mark paid orders for manual refund if KYC is explicitly rejected."""
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute("BEGIN IMMEDIATE")
            async with conn.execute(
                """
                SELECT order_id FROM order_kyc_gate
                WHERE user_id=? AND state='awaiting_kyc'
                """,
                (int(user_id),),
            ) as cur:
                order_ids = [int(row[0]) for row in await cur.fetchall()]
            for order_id in order_ids:
                await conn.execute(
                    "UPDATE orders SET status='refund_pending',updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='paid_awaiting_kyc'",
                    (order_id,),
                )
                await conn.execute(
                    """
                    UPDATE order_kyc_gate
                    SET state='refund_pending',reviewed_by=?,reviewed_at=CURRENT_TIMESTAMP,
                        updated_at=CURRENT_TIMESTAMP
                    WHERE order_id=?
                    """,
                    (int(reviewer_id), order_id),
                )
            await conn.commit()
        return order_ids


order_kyc_gate = OrderKycGate()
