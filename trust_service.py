from __future__ import annotations

from typing import Any

import aiosqlite

import config
from kyc_service import kyc_service


class TrustService:
    """User-level trust and automatic-payment policy.

    KYC state remains authoritative and separate. manual_trusted only means the
    operator explicitly chose to trust this Telegram identity without requiring
    document review. auto_approve is a permission flag; payment automation must
    still require a verified bank/payment match before issuing anything.
    """

    def __init__(self, db_path: str | None = None) -> None:
        self._db_path = db_path

    @property
    def db_path(self) -> str:
        return self._db_path or config.DATABASE_PATH

    async def ensure_schema(self) -> None:
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS user_trust (
                    user_id INTEGER PRIMARY KEY,
                    manual_trusted INTEGER NOT NULL DEFAULT 0 CHECK(manual_trusted IN (0,1)),
                    auto_approve INTEGER NOT NULL DEFAULT 0 CHECK(auto_approve IN (0,1)),
                    trusted_by INTEGER,
                    trusted_at TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS payment_verifications (
                    order_id INTEGER PRIMARY KEY,
                    state TEXT NOT NULL DEFAULT 'unverified'
                        CHECK(state IN ('unverified','matched','mismatch','manual_review')),
                    provider TEXT,
                    provider_transaction_id TEXT,
                    matched_amount_toman INTEGER,
                    matched_at TIMESTAMP,
                    metadata_json TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE UNIQUE INDEX IF NOT EXISTS idx_payment_provider_tx_unique
                ON payment_verifications(provider, provider_transaction_id)
                WHERE provider_transaction_id IS NOT NULL;
                """
            )
            await conn.commit()

    async def get(self, user_id: int) -> dict[str, Any]:
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                "SELECT * FROM user_trust WHERE user_id=?",
                (int(user_id),),
            ) as cur:
                row = await cur.fetchone()
        if row:
            return dict(row)
        return {
            "user_id": int(user_id),
            "manual_trusted": 0,
            "auto_approve": 0,
            "trusted_by": None,
            "trusted_at": None,
            "updated_at": None,
        }

    async def set_manual_trusted(self, user_id: int, trusted: bool, actor_id: int) -> None:
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                """
                INSERT INTO user_trust(
                    user_id,manual_trusted,auto_approve,trusted_by,trusted_at,updated_at
                ) VALUES(?,?,0,?,CASE WHEN ?=1 THEN CURRENT_TIMESTAMP ELSE NULL END,CURRENT_TIMESTAMP)
                ON CONFLICT(user_id) DO UPDATE SET
                    manual_trusted=excluded.manual_trusted,
                    trusted_by=excluded.trusted_by,
                    trusted_at=CASE WHEN excluded.manual_trusted=1 THEN CURRENT_TIMESTAMP ELSE NULL END,
                    auto_approve=CASE WHEN excluded.manual_trusted=0 THEN 0 ELSE user_trust.auto_approve END,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (int(user_id), 1 if trusted else 0, int(actor_id), 1 if trusted else 0),
            )
            await conn.commit()

    async def set_auto_approve(self, user_id: int, enabled: bool) -> None:
        await self.ensure_schema()
        if enabled and not await self.is_identity_approved(user_id):
            raise ValueError("user must be KYC-approved or manually trusted")
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                """
                INSERT INTO user_trust(user_id,manual_trusted,auto_approve,updated_at)
                VALUES(?,0,?,CURRENT_TIMESTAMP)
                ON CONFLICT(user_id) DO UPDATE SET
                    auto_approve=excluded.auto_approve,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (int(user_id), 1 if enabled else 0),
            )
            await conn.commit()

    async def is_manually_trusted(self, user_id: int) -> bool:
        return bool(int((await self.get(user_id)).get("manual_trusted") or 0))

    async def is_identity_approved(self, user_id: int) -> bool:
        if await self.is_manually_trusted(user_id):
            return True
        return await kyc_service.is_approved(int(user_id))

    async def auto_approve_enabled(self, user_id: int) -> bool:
        row = await self.get(user_id)
        return bool(int(row.get("auto_approve") or 0))

    async def can_auto_approve(self, user_id: int, order_id: int | None = None) -> bool:
        if not await self.is_identity_approved(user_id):
            return False
        if not await self.auto_approve_enabled(user_id):
            return False
        # Never auto-issue from an image receipt alone. A bank/payment adapter
        # must have matched a real transaction to this exact order first.
        if order_id is None:
            return False
        verification = await self.payment_verification(int(order_id))
        return bool(verification and verification.get("state") == "matched")

    async def set_payment_verification(
        self,
        order_id: int,
        state: str,
        *,
        provider: str | None = None,
        provider_transaction_id: str | None = None,
        matched_amount_toman: int | None = None,
        metadata_json: str | None = None,
    ) -> None:
        if state not in {"unverified", "matched", "mismatch", "manual_review"}:
            raise ValueError("invalid payment verification state")
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                """
                INSERT INTO payment_verifications(
                    order_id,state,provider,provider_transaction_id,matched_amount_toman,
                    matched_at,metadata_json,updated_at
                ) VALUES(?,?,?,?,?,
                    CASE WHEN ?='matched' THEN CURRENT_TIMESTAMP ELSE NULL END,
                    ?,CURRENT_TIMESTAMP)
                ON CONFLICT(order_id) DO UPDATE SET
                    state=excluded.state,
                    provider=excluded.provider,
                    provider_transaction_id=excluded.provider_transaction_id,
                    matched_amount_toman=excluded.matched_amount_toman,
                    matched_at=CASE WHEN excluded.state='matched' THEN CURRENT_TIMESTAMP ELSE NULL END,
                    metadata_json=excluded.metadata_json,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (
                    int(order_id), state, provider, provider_transaction_id,
                    matched_amount_toman, state, metadata_json,
                ),
            )
            await conn.commit()

    async def payment_verification(self, order_id: int) -> dict[str, Any] | None:
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                "SELECT * FROM payment_verifications WHERE order_id=?",
                (int(order_id),),
            ) as cur:
                row = await cur.fetchone()
        return dict(row) if row else None


trust_service = TrustService()
