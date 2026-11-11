"""Idempotent PAYG balance and suspension alerts keyed by Telegram user ID.

Status episodes are keyed by PAYG account ID (one Rebecca panel per account).
No alert is marked delivered until Telegram accepts the private notification.
"""
from __future__ import annotations

import aiosqlite

import config


BALANCE_THRESHOLDS = (200, 100, 50)  # toman, decreasing severity


def _rank(balance_toman: int) -> int:
    return sum(int(balance_toman) <= threshold for threshold in BALANCE_THRESHOLDS)


class PaygNotificationService:
    def __init__(self, db_path: str | None = None):
        self._db_path = db_path

    @property
    def db_path(self) -> str:
        return self._db_path or config.DATABASE_PATH

    async def ensure_schema(self) -> None:
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS payg_wallet_notice_state (
                    telegram_user_id INTEGER PRIMARY KEY CHECK(telegram_user_id > 0),
                    threshold_rank INTEGER NOT NULL DEFAULT 0,
                    last_balance_toman INTEGER,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS payg_status_notice_state (
                    account_id INTEGER PRIMARY KEY CHECK(account_id > 0),
                    last_notified_status TEXT NOT NULL
                        CHECK(last_notified_status IN ('active','suspended')),
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
            await conn.commit()

    async def due_wallet_threshold(self, user_id: int, balance_toman: int) -> int | None:
        await self.ensure_schema()
        user_id = int(user_id)
        balance_toman = max(0, int(balance_toman))
        current_rank = _rank(balance_toman)
        async with aiosqlite.connect(self.db_path) as conn:
            async with conn.execute(
                "SELECT threshold_rank FROM payg_wallet_notice_state WHERE telegram_user_id=?",
                (user_id,),
            ) as cur:
                row = await cur.fetchone()
            previous_rank = int(row[0]) if row else 0
            if current_rank == 0:
                # Topup above all thresholds resets the cycle.
                await conn.execute(
                    """
                    INSERT INTO payg_wallet_notice_state
                        (telegram_user_id,threshold_rank,last_balance_toman)
                    VALUES(?,0,?)
                    ON CONFLICT(telegram_user_id) DO UPDATE SET
                        threshold_rank=0,last_balance_toman=excluded.last_balance_toman,
                        updated_at=CURRENT_TIMESTAMP
                    """, (user_id, balance_toman),
                )
                await conn.commit()
                return None
        if current_rank <= previous_rank:
            return None
        # One notification for the most urgent crossed level avoids a burst if
        # several thresholds are crossed between consecutive billing polls.
        return BALANCE_THRESHOLDS[current_rank - 1]

    async def mark_wallet_notified(self, user_id: int, balance_toman: int) -> None:
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                """
                INSERT INTO payg_wallet_notice_state
                    (telegram_user_id,threshold_rank,last_balance_toman)
                VALUES(?,?,?)
                ON CONFLICT(telegram_user_id) DO UPDATE SET
                    threshold_rank=MAX(threshold_rank,excluded.threshold_rank),
                    last_balance_toman=excluded.last_balance_toman,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (int(user_id), _rank(int(balance_toman)), max(0,int(balance_toman))),
            )
            await conn.commit()

    async def should_notify_status(self, account_id: int, status: str) -> bool:
        await self.ensure_schema()
        if status not in {"active", "suspended"}:
            return False
        async with aiosqlite.connect(self.db_path) as conn:
            async with conn.execute(
                "SELECT last_notified_status FROM payg_status_notice_state WHERE account_id=?",
                (int(account_id),),
            ) as cur:
                row = await cur.fetchone()
            if row:
                return row[0] != status
            if status == "active":
                # Normal first run: do not send an activation notification.
                await conn.execute(
                    "INSERT OR IGNORE INTO payg_status_notice_state(account_id,last_notified_status) VALUES(?,'active')",
                    (int(account_id),),
                )
                await conn.commit()
                return False
            # An already suspended account must be announced once even when
            # this worker missed the original transition.
            return True

    async def mark_status_notified(self, account_id: int, status: str) -> None:
        if status not in {"active", "suspended"}:
            raise ValueError("invalid PAYG status notification")
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                """
                INSERT INTO payg_status_notice_state(account_id,last_notified_status)
                VALUES(?,?)
                ON CONFLICT(account_id) DO UPDATE SET
                    last_notified_status=excluded.last_notified_status,
                    updated_at=CURRENT_TIMESTAMP
                """, (int(account_id), status),
            )
            await conn.commit()


payg_notification_service = PaygNotificationService()
