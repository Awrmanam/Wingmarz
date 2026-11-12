"""Crash-resistant, fail-closed journal for paid Rebecca admin renewals.

One order gets at most one remote mutation attempt. An ambiguous HTTP/SQLite
failure never triggers an automatic second increment or another usage reset.
Pending interventions remain visible through list_needing_review().
"""
from __future__ import annotations

import aiosqlite
import config


class RenewalJournalError(RuntimeError):
    pass


class RebeccaRenewalJournal:
    def __init__(self, db_path: str | None = None):
        self._db_path = db_path

    @property
    def db_path(self) -> str:
        return self._db_path or config.DATABASE_PATH

    async def ensure_schema(self) -> None:
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.executescript("""
                CREATE TABLE IF NOT EXISTS rebecca_renewal_jobs (
                    order_id INTEGER PRIMARY KEY,
                    admin_id INTEGER NOT NULL,
                    actor_telegram_id INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'processing'
                        CHECK(status IN ('processing','completed','needs_review')),
                    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    finished_at TIMESTAMP,
                    error_type TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_rebecca_renewal_one_active_admin
                ON rebecca_renewal_jobs(admin_id)
                WHERE status IN ('processing','needs_review');
                CREATE INDEX IF NOT EXISTS idx_rebecca_renewal_review
                ON rebecca_renewal_jobs(status,updated_at);
            """)
            await conn.commit()

    async def reserve(self, order_id: int, admin_id: int, actor_id: int) -> None:
        """Fence duplicate callbacks and concurrent renewals of one panel."""
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute("BEGIN IMMEDIATE")
            async with conn.execute(
                """SELECT o.status,o.order_type,o.target_admin_id,
                          o.user_id,a.user_id
                   FROM orders o
                   JOIN admins a ON a.id=o.target_admin_id
                   WHERE o.id=?""",
                (int(order_id),),
            ) as cur:
                order = await cur.fetchone()
            if (not order or str(order[0]) not in {'pending','submitted','failed'}
                    or str(order[1]) != 'renew' or int(order[2] or 0) != int(admin_id)
                    or int(order[3]) != int(order[4])):
                await conn.rollback()
                raise RenewalJournalError("سفارش تمدید دیگر قابل اجرا نیست.")
            try:
                cur = await conn.execute(
                    """INSERT INTO rebecca_renewal_jobs(
                        order_id,admin_id,actor_telegram_id,status
                    ) VALUES(?,?,?,'processing')""",
                    (int(order_id), int(admin_id), int(actor_id)),
                )
                if cur.rowcount != 1:
                    raise RenewalJournalError("تمدید رزرو نشد.")
                await conn.commit()
            except aiosqlite.IntegrityError as exc:
                await conn.rollback()
                raise RenewalJournalError(
                    "این سفارش قبلاً اجرا شده یا تمدید دیگری برای همین پنل "
                    "در حال اجرا/بررسی است؛ برای جلوگیری از تمدید تکراری، "
                    "ابتدا وضعیت Rebecca و سفارش بررسی شود."
                ) from exc

    async def finalize(
        self, order_id: int, admin_id: int, actor_id: int, local_fields: dict
    ) -> None:
        """Atomically persist verified local limits, order approval and job result."""
        allowed = {
            'max_total_traffic', 'max_total_time', 'max_users',
            'validity_days', 'origin_plan_id', 'created_at',
            'traffic_cumulative_bytes', 'traffic_last_raw_bytes',
        }
        if not local_fields or set(local_fields) - allowed:
            raise RenewalJournalError("فیلدهای محلی تمدید نامعتبر هستند.")
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            try:
                await conn.execute("BEGIN IMMEDIATE")
                async with conn.execute(
                    "SELECT status,admin_id FROM rebecca_renewal_jobs WHERE order_id=?",
                    (int(order_id),),
                ) as cur:
                    job = await cur.fetchone()
                if not job or job[0] != "processing" or int(job[1]) != int(admin_id):
                    raise RenewalJournalError("رزرو معتبر تمدید یافت نشد.")
                clauses = ", ".join(f"{key}=?" for key in local_fields)
                values = list(local_fields.values())
                cur = await conn.execute(
                    f"UPDATE admins SET {clauses},updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (*values, int(admin_id)),
                )
                if cur.rowcount != 1:
                    raise RenewalJournalError("پنل محلی برای ثبت تمدید یافت نشد.")
                cur = await conn.execute("""
                    UPDATE orders SET status='approved',approved_by=?,
                        updated_at=CURRENT_TIMESTAMP
                    WHERE id=? AND order_type='renew' AND target_admin_id=?
                      AND status IN ('pending','submitted','failed')
                """, (int(actor_id), int(order_id), int(admin_id)))
                if cur.rowcount != 1:
                    raise RenewalJournalError("ثبت وضعیت سفارش تمدید ناموفق بود.")
                cur = await conn.execute("""
                    UPDATE rebecca_renewal_jobs
                    SET status='completed',finished_at=CURRENT_TIMESTAMP,
                        updated_at=CURRENT_TIMESTAMP
                    WHERE order_id=? AND status='processing'
                """, (int(order_id),))
                if cur.rowcount != 1:
                    raise RenewalJournalError("ثبت پایان عملیات تمدید ناموفق بود.")
                await conn.commit()
            except Exception:
                await conn.rollback()
                raise

    async def needs_review(self, order_id: int, error: BaseException) -> None:
        """Retain an ambiguous remote outcome for operator reconciliation."""
        # Only error category; never store provider HTTP payloads or secrets.
        error_type = type(error).__name__[:80]
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute("""
                UPDATE rebecca_renewal_jobs
                SET status='needs_review',error_type=?,updated_at=CURRENT_TIMESTAMP
                WHERE order_id=? AND status='processing'
            """, (error_type, int(order_id)))
            await conn.commit()

    async def list_needing_review(self, limit: int = 100) -> list[dict]:
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute("""
                SELECT order_id,admin_id,actor_telegram_id,status,
                       error_type,started_at,updated_at
                FROM rebecca_renewal_jobs
                WHERE status IN ('needs_review','processing')
                ORDER BY updated_at DESC LIMIT ?
            """, (max(1, min(int(limit), 500)),)) as cur:
                return [dict(row) for row in await cur.fetchall()]


rebecca_renewal_journal = RebeccaRenewalJournal()
