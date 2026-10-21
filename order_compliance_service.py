from __future__ import annotations

from typing import Any

import aiosqlite

import config


class OrderComplianceService:
    """Payment/KYC gate state without expanding the legacy orders status enum."""

    def __init__(self, db_path: str | None = None) -> None:
        self._db_path = db_path

    @property
    def db_path(self) -> str:
        return self._db_path or config.DATABASE_PATH

    async def ensure_schema(self) -> None:
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS order_compliance (
                    order_id INTEGER PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    payment_confirmed INTEGER NOT NULL DEFAULT 0
                        CHECK(payment_confirmed IN (0,1)),
                    payment_confirmed_by INTEGER,
                    payment_confirmed_at TIMESTAMP,
                    kyc_state TEXT NOT NULL DEFAULT 'awaiting'
                        CHECK(kyc_state IN ('not_required','awaiting','approved','rejected')),
                    refund_state TEXT NOT NULL DEFAULT 'none'
                        CHECK(refund_state IN ('none','pending','refunded')),
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE INDEX IF NOT EXISTS idx_order_compliance_user
                    ON order_compliance(user_id, payment_confirmed, kyc_state);
                """
            )
            await conn.commit()

    async def note_receipt(self, order_id: int, user_id: int, *, kyc_required: bool) -> None:
        await self.ensure_schema()
        kyc_state = "awaiting" if kyc_required else "not_required"
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                """
                INSERT INTO order_compliance(order_id,user_id,kyc_state,updated_at)
                VALUES(?,?,?,CURRENT_TIMESTAMP)
                ON CONFLICT(order_id) DO UPDATE SET
                    user_id=excluded.user_id,
                    kyc_state=CASE
                        WHEN order_compliance.kyc_state IN ('approved','rejected') THEN order_compliance.kyc_state
                        WHEN excluded.kyc_state='not_required' THEN 'not_required'
                        ELSE 'awaiting'
                    END,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (int(order_id), int(user_id), kyc_state),
            )
            await conn.commit()

    async def has_kyc_case(self, user_id: int) -> bool:
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            async with conn.execute(
                """
                SELECT 1 FROM order_compliance
                WHERE user_id=? AND kyc_state IN ('awaiting','rejected')
                LIMIT 1
                """,
                (int(user_id),),
            ) as cur:
                row = await cur.fetchone()
        return row is not None

    async def get(self, order_id: int) -> dict[str, Any] | None:
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                "SELECT * FROM order_compliance WHERE order_id=?",
                (int(order_id),),
            ) as cur:
                row = await cur.fetchone()
        return dict(row) if row else None

    async def confirm_payment(
        self,
        order_id: int,
        user_id: int,
        actor_id: int,
        *,
        kyc_required: bool,
    ) -> None:
        await self.ensure_schema()
        kyc_state = "awaiting" if kyc_required else "not_required"
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                """
                INSERT INTO order_compliance(
                    order_id,user_id,payment_confirmed,payment_confirmed_by,
                    payment_confirmed_at,kyc_state,refund_state,updated_at
                ) VALUES(?,?,1,?,CURRENT_TIMESTAMP,?,'none',CURRENT_TIMESTAMP)
                ON CONFLICT(order_id) DO UPDATE SET
                    user_id=excluded.user_id,
                    payment_confirmed=1,
                    payment_confirmed_by=excluded.payment_confirmed_by,
                    payment_confirmed_at=COALESCE(order_compliance.payment_confirmed_at,CURRENT_TIMESTAMP),
                    kyc_state=CASE
                        WHEN order_compliance.kyc_state IN ('approved','rejected') THEN order_compliance.kyc_state
                        WHEN excluded.kyc_state='not_required' THEN 'not_required'
                        ELSE 'awaiting'
                    END,
                    refund_state=CASE
                        WHEN order_compliance.refund_state='refunded' THEN 'refunded'
                        WHEN order_compliance.kyc_state='rejected' THEN 'pending'
                        ELSE 'none'
                    END,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (int(order_id), int(user_id), int(actor_id), kyc_state),
            )
            await conn.commit()

    async def awaiting_kyc_orders(self, user_id: int) -> list[int]:
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                """
                SELECT order_id FROM order_compliance
                WHERE user_id=? AND payment_confirmed=1 AND kyc_state='awaiting'
                ORDER BY order_id
                """,
                (int(user_id),),
            ) as cur:
                rows = await cur.fetchall()
        return [int(row["order_id"]) for row in rows]

    async def has_awaiting_kyc(self, user_id: int) -> bool:
        return bool(await self.awaiting_kyc_orders(user_id))

    async def reset_kyc_pending(self, user_id: int) -> None:
        """A resubmitted document re-opens rejected KYC unless money was already refunded."""
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                """
                UPDATE order_compliance
                SET kyc_state='awaiting',
                    refund_state=CASE WHEN refund_state='refunded' THEN 'refunded' ELSE 'none' END,
                    updated_at=CURRENT_TIMESTAMP
                WHERE user_id=? AND kyc_state='rejected' AND refund_state!='refunded'
                """,
                (int(user_id),),
            )
            await conn.commit()

    async def mark_kyc_approved(self, user_id: int) -> list[int]:
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                """
                SELECT order_id FROM order_compliance
                WHERE user_id=? AND payment_confirmed=1 AND kyc_state='awaiting'
                ORDER BY order_id
                """,
                (int(user_id),),
            ) as cur:
                rows = await cur.fetchall()
            ids = [int(row["order_id"]) for row in rows]
            await conn.execute(
                """
                UPDATE order_compliance
                SET kyc_state='approved',refund_state='none',updated_at=CURRENT_TIMESTAMP
                WHERE user_id=? AND kyc_state='awaiting'
                """,
                (int(user_id),),
            )
            await conn.commit()
        return ids

    async def mark_kyc_rejected(self, user_id: int) -> list[int]:
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                """
                SELECT order_id FROM order_compliance
                WHERE user_id=? AND payment_confirmed=1 AND kyc_state='awaiting'
                ORDER BY order_id
                """,
                (int(user_id),),
            ) as cur:
                rows = await cur.fetchall()
            ids = [int(row["order_id"]) for row in rows]
            await conn.execute(
                """
                UPDATE order_compliance
                SET kyc_state='rejected',
                    refund_state=CASE WHEN payment_confirmed=1 THEN 'pending' ELSE 'none' END,
                    updated_at=CURRENT_TIMESTAMP
                WHERE user_id=? AND kyc_state='awaiting'
                """,
                (int(user_id),),
            )
            await conn.commit()
        return ids

    async def ready_for_issue(self, order_id: int) -> bool:
        row = await self.get(order_id)
        if not row:
            return False
        return bool(
            int(row.get("payment_confirmed") or 0)
            and str(row.get("kyc_state")) in {"approved", "not_required"}
            and str(row.get("refund_state")) != "refunded"
        )


order_compliance_service = OrderComplianceService()
