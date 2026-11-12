"""Restart-safe Telegram broadcasts with per-recipient durable delivery receipts.

Delivery is at-least-once: Telegram does not expose an idempotency key, so a
crash after Telegram accepts a message but before local ACK may duplicate it.
Each recipient is leased under SQLite BEGIN IMMEDIATE to prevent normal races.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

import aiosqlite

import config


class BroadcastError(ValueError):
    pass


class BroadcastOutbox:
    def __init__(self, db_path: str | None = None):
        self._db_path = db_path

    @property
    def db_path(self) -> str:
        return self._db_path or config.DATABASE_PATH

    async def ensure_schema(self) -> None:
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.executescript("""
                CREATE TABLE IF NOT EXISTS broadcast_jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_key TEXT NOT NULL UNIQUE,
                    creator_telegram_id INTEGER NOT NULL,
                    audience TEXT NOT NULL CHECK(audience IN ('all','active')),
                    text TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'running'
                        CHECK(status IN ('running','paused','completed')),
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS broadcast_deliveries (
                    job_id INTEGER NOT NULL,
                    recipient_telegram_id INTEGER NOT NULL,
                    state TEXT NOT NULL DEFAULT 'pending'
                        CHECK(state IN ('pending','sending','sent','failed')),
                    attempts INTEGER NOT NULL DEFAULT 0,
                    retry_at INTEGER NOT NULL DEFAULT 0,
                    lease_token TEXT,
                    lease_until INTEGER NOT NULL DEFAULT 0,
                    delivered_at INTEGER,
                    last_error TEXT,
                    PRIMARY KEY(job_id,recipient_telegram_id)
                );
                CREATE INDEX IF NOT EXISTS idx_broadcast_deliveries_due
                    ON broadcast_deliveries(state,retry_at,lease_until,job_id);
                CREATE INDEX IF NOT EXISTS idx_broadcast_jobs_created
                    ON broadcast_jobs(created_at DESC);
            """)
            await conn.commit()

    async def create_job(
        self, *, actor_id: int, request_key: str, audience: str,
        text: str, recipient_ids: list[int], now: int | None = None,
    ) -> int:
        if int(actor_id) not in config.SUDO_ADMINS:
            raise BroadcastError("broadcast operator permission required")
        if audience not in {"all","active"}:
            raise BroadcastError("invalid broadcast audience")
        message = str(text or "").strip()
        if not message or len(message) > 3500:
            raise BroadcastError("پیام باید بین ۱ تا ۳۵۰۰ کاراکتر باشد.")
        unique = sorted({int(uid) for uid in recipient_ids if int(uid) > 0})
        if not unique:
            raise BroadcastError("گیرنده معتبری پیدا نشد.")
        key = str(request_key or "")
        if not 4 <= len(key) <= 180:
            raise BroadcastError("invalid broadcast request key")
        await self.ensure_schema()
        instant = int(now if now is not None else time.time())
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute("BEGIN IMMEDIATE")
            async with conn.execute(
                "SELECT id FROM broadcast_jobs WHERE request_key=?", (key,),
            ) as cur:
                old = await cur.fetchone()
            if old:
                await conn.rollback()
                return int(old[0])
            cur = await conn.execute(
                "INSERT INTO broadcast_jobs(request_key,creator_telegram_id,"
                "audience,text,status,created_at,updated_at) "
                "VALUES(?,?,?,?,'running',?,?)",
                (key,int(actor_id),audience,message,instant,instant),
            )
            job_id = int(cur.lastrowid)
            await conn.executemany(
                "INSERT INTO broadcast_deliveries(job_id,recipient_telegram_id)"
                " VALUES(?,?)",
                [(job_id,uid) for uid in unique],
            )
            await conn.commit()
        return job_id

    async def get_job(self, job_id: int) -> dict[str, Any] | None:
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                "SELECT j.id,j.creator_telegram_id,j.audience,j.status,"
                "j.created_at,j.updated_at,"
                "COUNT(d.recipient_telegram_id) AS total,"
                "SUM(CASE WHEN d.state='sent' THEN 1 ELSE 0 END) AS sent,"
                "SUM(CASE WHEN d.state='failed' THEN 1 ELSE 0 END) AS failed,"
                "SUM(CASE WHEN d.state IN ('pending','sending') THEN 1 ELSE 0 END) AS queued "
                "FROM broadcast_jobs j JOIN broadcast_deliveries d ON d.job_id=j.id "
                "WHERE j.id=? GROUP BY j.id",
                (int(job_id),),
            ) as cur:
                row = await cur.fetchone()
        return dict(row) if row else None

    async def list_jobs(self, limit: int = 12) -> list[dict[str, Any]]:
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            async with conn.execute(
                "SELECT id FROM broadcast_jobs ORDER BY id DESC LIMIT ?",
                (max(1,min(int(limit),30)),),
            ) as cur:
                ids = [int(item[0]) for item in await cur.fetchall()]
        return [row for uid in ids if (row:=await self.get_job(uid)) is not None]

    async def set_paused(self, job_id: int, actor_id: int, paused: bool) -> bool:
        if int(actor_id) not in config.SUDO_ADMINS:
            raise BroadcastError("broadcast operator permission required")
        await self.ensure_schema()
        now = int(time.time())
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute("BEGIN IMMEDIATE")
            cur = await conn.execute(
                "UPDATE broadcast_jobs SET status=?,updated_at=? "
                "WHERE id=? AND status IN ('running','paused')",
                ("paused" if paused else "running",now,int(job_id)),
            )
            await conn.commit()
            return cur.rowcount > 0

    async def claim_due(self, now: int | None = None, limit: int = 20) -> tuple[str,list[dict[str, Any]]]:
        await self.ensure_schema()
        instant = int(now if now is not None else time.time())
        token = uuid.uuid4().hex
        cap = max(1,min(int(limit),40))
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            await conn.execute("BEGIN IMMEDIATE")
            async with conn.execute(
                "SELECT d.job_id,d.recipient_telegram_id,j.text "
                "FROM broadcast_deliveries d "
                "JOIN broadcast_jobs j ON j.id=d.job_id "
                "WHERE j.status='running' AND d.retry_at<=? "
                "AND (d.state='pending' OR "
                "(d.state='sending' AND d.lease_until<?)) "
                "ORDER BY d.job_id,d.recipient_telegram_id LIMIT ?",
                (instant,instant,cap),
            ) as cur:
                rows = [dict(row) for row in await cur.fetchall()]
            for row in rows:
                await conn.execute(
                    "UPDATE broadcast_deliveries SET state='sending',"
                    "lease_token=?,lease_until=? WHERE job_id=? "
                    "AND recipient_telegram_id=?",
                    (token,instant+180,int(row["job_id"]),
                     int(row["recipient_telegram_id"])),
                )
            await conn.commit()
        return token,rows

    async def requeue_paused(
        self, job_id: int, recipient_id: int, token: str,
    ) -> None:
        """Pausing a scheduled send is not a Telegram delivery failure."""
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute("BEGIN IMMEDIATE")
            await conn.execute(
                "UPDATE broadcast_deliveries SET state='pending',"
                "lease_token=NULL,lease_until=0 WHERE job_id=? "
                "AND recipient_telegram_id=? AND state='sending' "
                "AND lease_token=?",
                (int(job_id),int(recipient_id),token),
            )
            await conn.commit()

    async def finish(
        self, job_id: int, recipient_id: int, token: str,
        *, delivered: bool, now: int | None = None,
    ) -> None:
        instant = int(now if now is not None else time.time())
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute("BEGIN IMMEDIATE")
            if delivered:
                await conn.execute(
                    "UPDATE broadcast_deliveries SET state='sent',"
                    "delivered_at=?,lease_token=NULL,lease_until=0,last_error=NULL "
                    "WHERE job_id=? AND recipient_telegram_id=? "
                    "AND state='sending' AND lease_token=?",
                    (instant,int(job_id),int(recipient_id),token),
                )
            else:
                await conn.execute(
                    "UPDATE broadcast_deliveries SET "
                    "attempts=attempts+1,"
                    "state=CASE WHEN attempts>=9 THEN 'failed' ELSE 'pending' END,"
                    "retry_at=?+MIN(3600,30*(1 << MIN(attempts,7))),"
                    "lease_token=NULL,lease_until=0,last_error='Telegram delivery failed' "
                    "WHERE job_id=? AND recipient_telegram_id=? "
                    "AND state='sending' AND lease_token=?",
                    (instant,int(job_id),int(recipient_id),token),
                )
            # Completed means all receipts have reached a terminal state,
            # whether successful or failed. The summary exposes both counts.
            await conn.execute(
                "UPDATE broadcast_jobs SET status='completed',updated_at=? "
                "WHERE id=? AND status='running' AND NOT EXISTS("
                "SELECT 1 FROM broadcast_deliveries d WHERE d.job_id=? "
                "AND d.state IN ('pending','sending'))",
                (instant,int(job_id),int(job_id)),
            )
            await conn.commit()

    async def process(self, bot, *, now: int | None = None, limit: int = 20) -> int:
        token, rows = await self.claim_due(now,limit)
        delivered = 0
        for item in rows:
            try:
                # Prevent one obvious pause race; a send already in flight
                # before a pause can still be delivered.
                status = await self.get_job(int(item["job_id"]))
                if not status or status["status"] == "paused":
                    await self.requeue_paused(
                        int(item["job_id"]),int(item["recipient_telegram_id"]),
                        token,
                    )
                    continue
                await bot.send_message(
                    chat_id=int(item["recipient_telegram_id"]),
                    text=str(item["text"]),
                    parse_mode=None,
                )
            except Exception:
                await self.finish(
                    int(item["job_id"]),int(item["recipient_telegram_id"]),
                    token,delivered=False,now=now,
                )
            else:
                await self.finish(
                    int(item["job_id"]),int(item["recipient_telegram_id"]),
                    token,delivered=True,now=now,
                )
                delivered += 1
            if len(rows)>1:
                await asyncio.sleep(0.05)
        return delivered


broadcast_outbox = BroadcastOutbox()
