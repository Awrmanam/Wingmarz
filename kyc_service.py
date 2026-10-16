from __future__ import annotations

from typing import Any

import aiosqlite

import config


VALID_KYC_STATUSES = {"pending", "approved", "rejected"}


class KycService:
    """Minimal persisted KYC state and per-panel enforcement policy.

    Identity documents are kept as Telegram file identifiers only; Wingmarz
    does not download or duplicate the binary document into its own storage.
    Missing per-admin policy means KYC is required by default.
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
                CREATE TABLE IF NOT EXISTS kyc_profiles (
                    user_id INTEGER PRIMARY KEY,
                    full_name TEXT NOT NULL,
                    document_file_id TEXT NOT NULL,
                    document_file_unique_id TEXT,
                    status TEXT NOT NULL DEFAULT 'pending'
                        CHECK(status IN ('pending','approved','rejected')),
                    rejection_reason TEXT,
                    submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    reviewed_at TIMESTAMP,
                    reviewed_by INTEGER,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS admin_kyc_policy (
                    admin_id INTEGER PRIMARY KEY,
                    kyc_required INTEGER NOT NULL DEFAULT 1
                        CHECK(kyc_required IN (0,1)),
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE INDEX IF NOT EXISTS idx_kyc_profiles_status
                    ON kyc_profiles(status);
                """
            )
            await conn.commit()

    async def get_profile(self, user_id: int) -> dict[str, Any] | None:
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                "SELECT * FROM kyc_profiles WHERE user_id=?",
                (int(user_id),),
            ) as cur:
                row = await cur.fetchone()
        return dict(row) if row else None

    async def status(self, user_id: int) -> str:
        profile = await self.get_profile(user_id)
        return str(profile.get("status")) if profile else "not_submitted"

    async def is_approved(self, user_id: int) -> bool:
        return await self.status(user_id) == "approved"

    async def submit(
        self,
        user_id: int,
        full_name: str,
        document_file_id: str,
        document_file_unique_id: str | None = None,
    ) -> None:
        full_name = " ".join(str(full_name or "").split()).strip()
        file_id = str(document_file_id or "").strip()
        if len(full_name) < 3 or len(full_name) > 120:
            raise ValueError("invalid full name")
        if not file_id:
            raise ValueError("document image is required")
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                """
                INSERT INTO kyc_profiles(
                    user_id,full_name,document_file_id,document_file_unique_id,
                    status,rejection_reason,submitted_at,reviewed_at,reviewed_by,updated_at
                )
                VALUES(?,?,?,?, 'pending', NULL, CURRENT_TIMESTAMP, NULL, NULL, CURRENT_TIMESTAMP)
                ON CONFLICT(user_id) DO UPDATE SET
                    full_name=excluded.full_name,
                    document_file_id=excluded.document_file_id,
                    document_file_unique_id=excluded.document_file_unique_id,
                    status='pending',
                    rejection_reason=NULL,
                    submitted_at=CURRENT_TIMESTAMP,
                    reviewed_at=NULL,
                    reviewed_by=NULL,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (
                    int(user_id),
                    full_name,
                    file_id,
                    str(document_file_unique_id or "").strip() or None,
                ),
            )
            await conn.commit()

    async def approve(self, user_id: int, reviewer_id: int) -> bool:
        return await self._review(user_id, reviewer_id, "approved", None)

    async def reject(self, user_id: int, reviewer_id: int, reason: str | None = None) -> bool:
        reason = " ".join(str(reason or "").split()).strip() or "مدرک ارسالی قابل تأیید نبود."
        return await self._review(user_id, reviewer_id, "rejected", reason)

    async def _review(
        self,
        user_id: int,
        reviewer_id: int,
        status: str,
        reason: str | None,
    ) -> bool:
        if status not in {"approved", "rejected"}:
            raise ValueError("invalid review status")
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            cur = await conn.execute(
                """
                UPDATE kyc_profiles
                SET status=?, rejection_reason=?, reviewed_at=CURRENT_TIMESTAMP,
                    reviewed_by=?, updated_at=CURRENT_TIMESTAMP
                WHERE user_id=? AND status='pending'
                """,
                (status, reason, int(reviewer_id), int(user_id)),
            )
            await conn.commit()
            return cur.rowcount > 0

    async def list_pending(self, limit: int = 100) -> list[dict[str, Any]]:
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                """
                SELECT * FROM kyc_profiles
                WHERE status='pending'
                ORDER BY submitted_at ASC
                LIMIT ?
                """,
                (max(1, min(int(limit), 500)),),
            ) as cur:
                rows = await cur.fetchall()
        return [dict(row) for row in rows]

    async def is_required(self, admin_id: int) -> bool:
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            async with conn.execute(
                "SELECT kyc_required FROM admin_kyc_policy WHERE admin_id=?",
                (int(admin_id),),
            ) as cur:
                row = await cur.fetchone()
        return bool(int(row[0])) if row else True

    async def set_required(self, admin_id: int, required: bool) -> None:
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                """
                INSERT INTO admin_kyc_policy(admin_id,kyc_required,updated_at)
                VALUES(?,?,CURRENT_TIMESTAMP)
                ON CONFLICT(admin_id) DO UPDATE SET
                    kyc_required=excluded.kyc_required,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (int(admin_id), 1 if required else 0),
            )
            await conn.commit()

    async def panel_access_allowed(self, user_id: int, admin_id: int) -> bool:
        if not await self.is_required(admin_id):
            return True
        return await self.is_approved(user_id)


kyc_service = KycService()
