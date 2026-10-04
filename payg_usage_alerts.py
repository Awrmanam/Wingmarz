from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import aiosqlite

import config
from payg_service import GIB


ALERT_STEP_GB = 10
ALERT_STEP_BYTES = ALERT_STEP_GB * GIB


@dataclass(frozen=True)
class UsageAlert:
    account_id: int
    user_id: int
    milestone_gb: int
    current_usage_bytes: int
    rate_toman_per_gb: int
    interval_cost_toman: int
    charged_total_toman: int
    balance_toman: int
    outstanding_toman: int

    @property
    def current_usage_gb(self) -> float:
        return self.current_usage_bytes / GIB


class PaygUsageAlertService:
    """Persisted 10 GiB usage milestones for PAYG accounts.

    Existing accounts are baselined once when this feature is first deployed so
    customers are not spammed with historical milestones. Accounts created after
    that point start from zero and receive alerts at 10, 20, 30 ... GiB.
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
                CREATE TABLE IF NOT EXISTS payg_usage_alert_state (
                    account_id INTEGER PRIMARY KEY,
                    last_milestone_gb INTEGER NOT NULL DEFAULT 0,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS payg_feature_meta (
                    feature_key TEXT PRIMARY KEY,
                    feature_value TEXT NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
            async with conn.execute(
                "SELECT feature_value FROM payg_feature_meta WHERE feature_key='usage_alerts_v1_initialized'"
            ) as cur:
                initialized = await cur.fetchone()

            if not initialized:
                # Do not send retroactive 10GB/20GB/... alerts to accounts that
                # already existed before this feature was deployed.
                await conn.execute(
                    """
                    INSERT OR IGNORE INTO payg_usage_alert_state(account_id,last_milestone_gb)
                    SELECT id, CAST(cumulative_usage_bytes / ? AS INTEGER) * ?
                    FROM payg_accounts
                    """,
                    (ALERT_STEP_BYTES, ALERT_STEP_GB),
                )
                await conn.execute(
                    """
                    INSERT OR REPLACE INTO payg_feature_meta(feature_key,feature_value,updated_at)
                    VALUES('usage_alerts_v1_initialized','1',CURRENT_TIMESTAMP)
                    """
                )
            await conn.commit()

    async def pending_alerts(self, account_id: int) -> list[UsageAlert]:
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                """
                SELECT id,user_id,status,price_per_gb_toman,cumulative_usage_bytes,
                       charged_toman_total
                FROM payg_accounts WHERE id=?
                """,
                (int(account_id),),
            ) as cur:
                row = await cur.fetchone()
            if not row or str(row["status"]) == "migrated":
                return []

            async with conn.execute(
                "SELECT last_milestone_gb FROM payg_usage_alert_state WHERE account_id=?",
                (int(account_id),),
            ) as cur:
                state = await cur.fetchone()
            last_milestone = int(state[0]) if state else 0

            cumulative = int(row["cumulative_usage_bytes"] or 0)
            current_milestone = (cumulative // ALERT_STEP_BYTES) * ALERT_STEP_GB
            if current_milestone <= last_milestone:
                return []

            rate = int(row["price_per_gb_toman"] or 0)
            charged_total = int(row["charged_toman_total"] or 0)
            target_charge = cumulative * rate // GIB if rate > 0 else 0
            outstanding = max(0, target_charge - charged_total)
            async with conn.execute(
                "SELECT balance_toman FROM wallets WHERE user_id=?",
                (int(row["user_id"]),),
            ) as cur:
                balance_row = await cur.fetchone()
            balance = int(balance_row[0]) if balance_row else 0

        return [
            UsageAlert(
                account_id=int(row["id"]),
                user_id=int(row["user_id"]),
                milestone_gb=milestone,
                current_usage_bytes=cumulative,
                rate_toman_per_gb=rate,
                interval_cost_toman=ALERT_STEP_GB * rate,
                charged_total_toman=charged_total,
                balance_toman=balance,
                outstanding_toman=outstanding,
            )
            for milestone in range(
                last_milestone + ALERT_STEP_GB,
                current_milestone + ALERT_STEP_GB,
                ALERT_STEP_GB,
            )
        ]

    async def mark_sent(self, account_id: int, milestone_gb: int) -> None:
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                """
                INSERT INTO payg_usage_alert_state(account_id,last_milestone_gb,updated_at)
                VALUES(?,?,CURRENT_TIMESTAMP)
                ON CONFLICT(account_id) DO UPDATE SET
                    last_milestone_gb=MAX(payg_usage_alert_state.last_milestone_gb,excluded.last_milestone_gb),
                    updated_at=CURRENT_TIMESTAMP
                """,
                (int(account_id), int(milestone_gb)),
            )
            await conn.commit()

    async def get_state(self, account_id: int) -> dict[str, Any] | None:
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                "SELECT * FROM payg_usage_alert_state WHERE account_id=?",
                (int(account_id),),
            ) as cur:
                row = await cur.fetchone()
        return dict(row) if row else None


payg_usage_alert_service = PaygUsageAlertService()
