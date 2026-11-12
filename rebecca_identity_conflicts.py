"""Durable, read-only review queue for Rebecca/Telegram ownership disagreements.

Discovery and /start synchronization must never transfer an existing local
panel to a different Telegram ID based solely on a mutable remote field.
Only an explicit owner-authorized transfer flow may do that in the future.
"""
from __future__ import annotations

from typing import Any

import aiosqlite

import config


class RebeccaIdentityConflicts:
    def __init__(self, db_path: str | None = None):
        self._db_path = db_path

    @property
    def db_path(self) -> str:
        return self._db_path or config.DATABASE_PATH

    async def ensure_schema(self) -> None:
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.executescript("""
                CREATE TABLE IF NOT EXISTS rebecca_identity_conflicts (
                    username TEXT PRIMARY KEY,
                    local_telegram_id INTEGER NOT NULL,
                    remote_telegram_id INTEGER NOT NULL,
                    state TEXT NOT NULL DEFAULT 'open'
                        CHECK(state IN ('open','resolved')),
                    occurrences INTEGER NOT NULL DEFAULT 1,
                    first_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    reviewed_by INTEGER,
                    reviewed_at TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_rebecca_identity_conflicts_state
                ON rebecca_identity_conflicts(state);
            """)
            await conn.commit()

    async def report(self, username: str, local_id: int, remote_id: int) -> None:
        """Upsert one mismatch; never change the owner of an existing panel."""
        username = str(username or "").strip()
        local_id, remote_id = int(local_id), int(remote_id)
        if not username or min(local_id, remote_id) <= 0 or local_id == remote_id:
            raise ValueError("A valid mismatch is required")
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute("""
                INSERT INTO rebecca_identity_conflicts
                    (username, local_telegram_id, remote_telegram_id)
                VALUES(?,?,?)
                ON CONFLICT(username) DO UPDATE SET
                    local_telegram_id=excluded.local_telegram_id,
                    remote_telegram_id=excluded.remote_telegram_id,
                    state='open', occurrences=occurrences+1,
                    last_seen_at=CURRENT_TIMESTAMP,
                    reviewed_by=NULL, reviewed_at=NULL
            """, (username, local_id, remote_id))
            await conn.commit()

    async def clear_when_matching(self, username: str, actual_owner_id: int) -> None:
        """Mark a historical conflict resolved only when both systems agree."""
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute("""
                UPDATE rebecca_identity_conflicts
                SET state='resolved',last_seen_at=CURRENT_TIMESTAMP
                WHERE username=? AND state='open'
                  AND local_telegram_id=?
                  AND remote_telegram_id=?
            """, (str(username), int(actual_owner_id), int(actual_owner_id)))
            # An owner-approved change can also resolve a previously mismatched
            # record once upstream/local IDs now agree.
            await conn.execute("""
                UPDATE rebecca_identity_conflicts
                SET state='resolved',last_seen_at=CURRENT_TIMESTAMP
                WHERE username=? AND state='open' AND (
                  local_telegram_id!=? OR remote_telegram_id!=?
                )
            """, (str(username), int(actual_owner_id), int(actual_owner_id)))
            await conn.commit()

    async def list_open(self, limit: int = 100) -> list[dict[str, Any]]:
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute("""
                SELECT username,local_telegram_id,remote_telegram_id,
                       occurrences,first_seen_at,last_seen_at
                FROM rebecca_identity_conflicts
                WHERE state='open' ORDER BY last_seen_at DESC LIMIT ?
            """, (max(1, min(int(limit), 500)),)) as cur:
                return [dict(row) for row in await cur.fetchall()]


rebecca_identity_conflicts = RebeccaIdentityConflicts()
