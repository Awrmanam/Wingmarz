from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import aiosqlite

import config


PAYMENT_AWAITING = "awaiting_payment"
PAYMENT_RECEIPT = "receipt_submitted"
PAYMENT_APPROVED = "approved"
PAYMENT_REJECTED = "rejected"

KYC_PENDING = "pending"
KYC_APPROVED = "approved"
KYC_REJECTED = "rejected"
KYC_NOT_REQUIRED = "not_required"

REFUND_NONE = "none"
REFUND_PENDING = "pending"
REFUND_DONE = "refunded"


@dataclass(frozen=True)
class OrderGate:
    order_id: int
    user_id: int
    payment_state: str
    kyc_required: bool
    kyc_state: str
    refund_state: str

    @property
    def ready_to_issue(self) -> bool:
        return (
            self.payment_state == PAYMENT_APPROVED
            and (
                not self.kyc_required
                or self.kyc_state in {KYC_APPROVED, KYC_NOT_REQUIRED}
            )
            and self.refund_state == REFUND_NONE
        )


class OrderComplianceService:
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
                    payment_state TEXT NOT NULL DEFAULT 'awaiting_payment',
                    kyc_required INTEGER NOT NULL DEFAULT 1 CHECK(kyc_required IN (0,1)),
                    kyc_state TEXT NOT NULL DEFAULT 'pending',
                    refund_state TEXT NOT NULL DEFAULT 'none',
                    payment_reviewed_by INTEGER,
                    payment_reviewed_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE INDEX IF NOT EXISTS idx_order_compliance_user
                ON order_compliance(user_id, payment_state, kyc_state);
                """
            )
            await conn.commit()

    @staticmethod
    def _gate(row: aiosqlite.Row | dict[str, Any] | None) -> OrderGate | None:
        if row is None:
            return None
        data = dict(row)
        return OrderGate(
            order_id=int(data["order_id"]),
            user_id=int(data["user_id"]),
            payment_state=str(data["payment_state"]),
            kyc_required=bool(int(data["kyc_required"])),
            kyc_state=str(data["kyc_state"]),
            refund_state=str(data["refund_state"]),
        )

    async def ensure_order(
        self,
        order_id: int,
        user_id: int,
        *,
        kyc_required: bool,
        payment_approved: bool = False,
    ) -> OrderGate:
        await self.ensure_schema()
        payment_state = PAYMENT_APPROVED if payment_approved else PAYMENT_AWAITING
        kyc_state = KYC_PENDING if kyc_required else KYC_NOT_REQUIRED
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            await conn.execute(
                """
                INSERT OR IGNORE INTO order_compliance(
                    order_id,user_id,payment_state,kyc_required,kyc_state,refund_state
                ) VALUES(?,?,?,?,?,'none')
                """,
                (
                    int(order_id),
                    int(user_id),
                    payment_state,
                    1 if kyc_required else 0,
                    kyc_state,
                ),
            )
            await conn.commit()
            async with conn.execute(
                "SELECT * FROM order_compliance WHERE order_id=?",
                (int(order_id),),
            ) as cur:
                row = await cur.fetchone()
        gate = self._gate(row)
        if gate is None:
            raise RuntimeError("order compliance row was not created")
        return gate

    async def get(self, order_id: int) -> OrderGate | None:
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                "SELECT * FROM order_compliance WHERE order_id=?",
                (int(order_id),),
            ) as cur:
                row = await cur.fetchone()
        return self._gate(row)

    async def mark_receipt_submitted(self, order_id: int, user_id: int) -> bool:
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            cur = await conn.execute(
                """
                UPDATE order_compliance
                SET payment_state=?, updated_at=CURRENT_TIMESTAMP
                WHERE order_id=? AND user_id=?
                  AND payment_state IN (?,?)
                  AND refund_state=?
                """,
                (
                    PAYMENT_RECEIPT,
                    int(order_id),
                    int(user_id),
                    PAYMENT_AWAITING,
                    PAYMENT_REJECTED,
                    REFUND_NONE,
                ),
            )
            await conn.commit()
            return cur.rowcount == 1

    async def approve_payment(
        self,
        order_id: int,
        actor_id: int,
        *,
        identity_approved: bool,
    ) -> OrderGate | None:
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            await conn.execute("BEGIN IMMEDIATE")
            async with conn.execute(
                "SELECT * FROM order_compliance WHERE order_id=?",
                (int(order_id),),
            ) as cur:
                row = await cur.fetchone()
            if not row:
                await conn.rollback()
                return None
            data = dict(row)
            if data["refund_state"] != REFUND_NONE:
                await conn.rollback()
                return self._gate(row)
            if data["payment_state"] not in {PAYMENT_RECEIPT, PAYMENT_APPROVED}:
                await conn.rollback()
                return self._gate(row)

            new_kyc_state = str(data["kyc_state"])
            if identity_approved:
                new_kyc_state = KYC_APPROVED if int(data["kyc_required"]) else KYC_NOT_REQUIRED

            await conn.execute(
                """
                UPDATE order_compliance
                SET payment_state=?, kyc_state=?, payment_reviewed_by=?,
                    payment_reviewed_at=CURRENT_TIMESTAMP,
                    updated_at=CURRENT_TIMESTAMP
                WHERE order_id=?
                """,
                (
                    PAYMENT_APPROVED,
                    new_kyc_state,
                    int(actor_id),
                    int(order_id),
                ),
            )
            await conn.commit()
            async with conn.execute(
                "SELECT * FROM order_compliance WHERE order_id=?",
                (int(order_id),),
            ) as cur:
                updated = await cur.fetchone()
        return self._gate(updated)

    async def reject_receipt(self, order_id: int, actor_id: int) -> OrderGate | None:
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            await conn.execute(
                """
                UPDATE order_compliance
                SET payment_state=?, payment_reviewed_by=?,
                    payment_reviewed_at=CURRENT_TIMESTAMP,
                    updated_at=CURRENT_TIMESTAMP
                WHERE order_id=? AND refund_state=?
                """,
                (PAYMENT_REJECTED, int(actor_id), int(order_id), REFUND_NONE),
            )
            await conn.commit()
            async with conn.execute(
                "SELECT * FROM order_compliance WHERE order_id=?",
                (int(order_id),),
            ) as cur:
                row = await cur.fetchone()
        return self._gate(row)

    async def reset_for_new_receipt(self, order_id: int) -> bool:
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            cur = await conn.execute(
                """
                UPDATE order_compliance
                SET payment_state=?, payment_reviewed_by=NULL,
                    payment_reviewed_at=NULL, updated_at=CURRENT_TIMESTAMP
                WHERE order_id=? AND payment_state=?
                """,
                (PAYMENT_AWAITING, int(order_id), PAYMENT_REJECTED),
            )
            await conn.commit()
            return cur.rowcount == 1

    async def set_order_kyc(self, order_id: int, approved: bool) -> OrderGate | None:
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            state = KYC_APPROVED if approved else KYC_REJECTED
            await conn.execute(
                """
                UPDATE order_compliance
                SET kyc_state=?,
                    refund_state=CASE
                        WHEN ?=? AND payment_state=? THEN ?
                        ELSE refund_state
                    END,
                    updated_at=CURRENT_TIMESTAMP
                WHERE order_id=? AND kyc_required=1
                """,
                (
                    state,
                    state,
                    KYC_REJECTED,
                    PAYMENT_APPROVED,
                    REFUND_PENDING,
                    int(order_id),
                ),
            )
            await conn.commit()
            async with conn.execute(
                "SELECT * FROM order_compliance WHERE order_id=?",
                (int(order_id),),
            ) as cur:
                row = await cur.fetchone()
        return self._gate(row)

    async def approve_user_kyc(self, user_id: int) -> list[int]:
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                """
                UPDATE order_compliance
                SET kyc_state=?, updated_at=CURRENT_TIMESTAMP
                WHERE user_id=? AND kyc_required=1
                  AND kyc_state IN (?,?)
                  AND refund_state=?
                """,
                (
                    KYC_APPROVED,
                    int(user_id),
                    KYC_PENDING,
                    KYC_REJECTED,
                    REFUND_NONE,
                ),
            )
            await conn.commit()
            async with conn.execute(
                """
                SELECT order_id FROM order_compliance
                WHERE user_id=? AND payment_state=? AND kyc_state=?
                  AND kyc_required=1 AND refund_state=?
                ORDER BY order_id
                """,
                (int(user_id), PAYMENT_APPROVED, KYC_APPROVED, REFUND_NONE),
            ) as cur:
                rows = await cur.fetchall()
        return [int(row[0]) for row in rows]

    async def reject_user_kyc(self, user_id: int) -> list[int]:
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                """
                UPDATE order_compliance
                SET kyc_state=?,
                    refund_state=CASE
                        WHEN payment_state=? THEN ?
                        ELSE refund_state
                    END,
                    updated_at=CURRENT_TIMESTAMP
                WHERE user_id=? AND kyc_required=1
                  AND kyc_state=?
                """,
                (
                    KYC_REJECTED,
                    PAYMENT_APPROVED,
                    REFUND_PENDING,
                    int(user_id),
                    KYC_PENDING,
                ),
            )
            await conn.commit()
            async with conn.execute(
                """
                SELECT order_id FROM order_compliance
                WHERE user_id=? AND payment_state=? AND kyc_state=?
                  AND refund_state=?
                ORDER BY order_id
                """,
                (int(user_id), PAYMENT_APPROVED, KYC_REJECTED, REFUND_PENDING),
            ) as cur:
                rows = await cur.fetchall()
        return [int(row[0]) for row in rows]

    async def awaiting_paid_kyc(self, user_id: int) -> list[int]:
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            async with conn.execute(
                """
                SELECT order_id FROM order_compliance
                WHERE user_id=? AND payment_state=? AND kyc_required=1
                  AND kyc_state IN (?,?) AND refund_state=?
                ORDER BY order_id
                """,
                (
                    int(user_id),
                    PAYMENT_APPROVED,
                    KYC_PENDING,
                    KYC_REJECTED,
                    REFUND_NONE,
                ),
            ) as cur:
                rows = await cur.fetchall()
        return [int(row[0]) for row in rows]

    async def expire_overdue_kyc(self, hours: int = 48) -> list[dict[str, int]]:
        """Move paid orders with unfinished KYC into the manual refund queue."""
        hours = max(1, int(hours))
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            await conn.execute("BEGIN IMMEDIATE")
            async with conn.execute(
                """
                SELECT order_id,user_id
                FROM order_compliance
                WHERE payment_state=?
                  AND kyc_required=1
                  AND kyc_state=?
                  AND refund_state=?
                  AND payment_reviewed_at IS NOT NULL
                  AND payment_reviewed_at <= datetime('now', ?)
                ORDER BY order_id
                """,
                (
                    PAYMENT_APPROVED,
                    KYC_PENDING,
                    REFUND_NONE,
                    f"-{hours} hours",
                ),
            ) as cur:
                rows = await cur.fetchall()

            if rows:
                ids = [int(row["order_id"]) for row in rows]
                placeholders = ",".join("?" for _ in ids)
                await conn.execute(
                    f"""
                    UPDATE order_compliance
                    SET refund_state=?, updated_at=CURRENT_TIMESTAMP
                    WHERE order_id IN ({placeholders})
                      AND refund_state=?
                    """,
                    (REFUND_PENDING, *ids, REFUND_NONE),
                )
            await conn.commit()
        return [
            {"order_id": int(row["order_id"]), "user_id": int(row["user_id"])}
            for row in rows
        ]

    async def mark_refunded(self, order_id: int) -> OrderGate | None:
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            await conn.execute(
                """
                UPDATE order_compliance
                SET refund_state=?, updated_at=CURRENT_TIMESTAMP
                WHERE order_id=? AND refund_state=?
                """,
                (REFUND_DONE, int(order_id), REFUND_PENDING),
            )
            await conn.commit()
            async with conn.execute(
                "SELECT * FROM order_compliance WHERE order_id=?",
                (int(order_id),),
            ) as cur:
                row = await cur.fetchone()
        return self._gate(row)


order_compliance_service = OrderComplianceService()
