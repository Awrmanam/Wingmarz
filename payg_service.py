from __future__ import annotations

from dataclasses import dataclass
import secrets
import time
from typing import Any

import aiosqlite

import config
from database import db
from models.schemas import AdminModel
from rebecca_api import RebeccaAPIError, RebeccaConflict, rebecca_api
from utils.rebecca import parse_service_ids


GIB = 1024 ** 3
_LONG_VALIDITY_DAYS = 36500


class PaygError(RuntimeError):
    pass


class PaygInsufficientFunds(PaygError):
    def __init__(self, shortfall_toman: int):
        super().__init__("insufficient wallet balance")
        self.shortfall_toman = max(0, int(shortfall_toman))


@dataclass(frozen=True)
class BillingResult:
    account_id: int
    user_id: int
    old_status: str
    new_status: str
    raw_usage_bytes: int
    cumulative_usage_bytes: int
    charged_now_toman: int
    charged_total_toman: int
    outstanding_toman: int
    balance_toman: int


@dataclass(frozen=True)
class MigrationResult:
    migration_id: int
    new_admin_id: int
    username: str
    password: str
    login_url: str
    plan_name: str


class PaygService:
    """Wallet-backed PAYG billing with fixed-plan migration.

    Money remains the source of truth. Usage is tracked in raw bytes and the
    cumulative due is calculated from the full byte count, avoiding incremental
    rounding drift. The wallet ledger is append-only and every balance mutation
    happens under an SQLite write lock.
    """

    def __init__(self, db_path: str | None = None):
        self._db_path = db_path

    @property
    def db_path(self) -> str:
        return self._db_path or config.DATABASE_PATH

    async def ensure_schema(self) -> None:
        default_services = ",".join(str(x) for x in getattr(config, "REBECCA_SERVICE_IDS", []) if int(x) > 0)
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS wallets (
                    user_id INTEGER PRIMARY KEY,
                    balance_toman INTEGER NOT NULL DEFAULT 0 CHECK(balance_toman >= 0),
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS wallet_ledger (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    amount_toman INTEGER NOT NULL,
                    balance_after_toman INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    reference_type TEXT,
                    reference_id INTEGER,
                    note TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_wallet_ledger_user
                    ON wallet_ledger(user_id, id DESC);

                CREATE TABLE IF NOT EXISTS payg_settings (
                    provider TEXT PRIMARY KEY,
                    enabled INTEGER NOT NULL DEFAULT 0,
                    display_name TEXT NOT NULL DEFAULT 'PAYG',
                    price_per_gb_toman INTEGER NOT NULL DEFAULT 0,
                    min_topup_toman INTEGER NOT NULL DEFAULT 100000,
                    max_users INTEGER NOT NULL DEFAULT 50,
                    service_ids TEXT NOT NULL DEFAULT '',
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS payg_accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    admin_id INTEGER NOT NULL UNIQUE,
                    provider TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    price_per_gb_toman INTEGER NOT NULL,
                    last_raw_usage_bytes INTEGER NOT NULL DEFAULT 0,
                    cumulative_usage_bytes INTEGER NOT NULL DEFAULT 0,
                    charged_toman_total INTEGER NOT NULL DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_payg_accounts_user
                    ON payg_accounts(user_id, provider, status);

                CREATE TABLE IF NOT EXISTS payg_topups (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    amount_toman INTEGER NOT NULL CHECK(amount_toman > 0),
                    provider TEXT NOT NULL DEFAULT 'rebecca',
                    purpose TEXT NOT NULL DEFAULT 'wallet',
                    account_id INTEGER,
                    migration_id INTEGER,
                    status TEXT NOT NULL DEFAULT 'pending',
                    receipt_file_id TEXT,
                    approved_by INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_payg_topups_user
                    ON payg_topups(user_id, id DESC);

                CREATE TABLE IF NOT EXISTS payg_migrations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    account_id INTEGER NOT NULL,
                    target_plan_id INTEGER NOT NULL,
                    price_toman INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'waiting_funds',
                    new_admin_id INTEGER,
                    error_message TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_payg_migrations_account
                    ON payg_migrations(account_id, id DESC);
                """
            )
            await conn.execute(
                """
                INSERT OR IGNORE INTO payg_settings(
                    provider,enabled,display_name,price_per_gb_toman,
                    min_topup_toman,max_users,service_ids
                ) VALUES('rebecca',0,'PAYG Rebecca',0,100000,50,?)
                """,
                (default_services,),
            )
            await conn.commit()

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------

    async def get_settings(self, provider: str = "rebecca") -> dict[str, Any] | None:
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                "SELECT * FROM payg_settings WHERE provider=?", (str(provider),)
            ) as cur:
                row = await cur.fetchone()
        return dict(row) if row else None

    async def update_settings(self, provider: str = "rebecca", **fields: Any) -> dict[str, Any]:
        allowed = {
            "enabled", "display_name", "price_per_gb_toman",
            "min_topup_toman", "max_users", "service_ids",
        }
        if not fields or any(key not in allowed for key in fields):
            raise PaygError("invalid PAYG settings update")
        if "enabled" in fields:
            fields["enabled"] = 1 if bool(fields["enabled"]) else 0
        for key in ("price_per_gb_toman", "min_topup_toman", "max_users"):
            if key in fields:
                fields[key] = int(fields[key])
                if fields[key] < (1 if key == "max_users" else 0):
                    raise PaygError(f"invalid {key}")
        if "service_ids" in fields:
            # Empty is allowed while disabled; enabled validation happens below.
            raw = str(fields["service_ids"] or "").strip()
            if raw:
                fields["service_ids"] = ",".join(str(x) for x in parse_service_ids(raw))
            else:
                fields["service_ids"] = ""
        await self.ensure_schema()
        setters = ",".join(f"{key}=?" for key in fields)
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                f"UPDATE payg_settings SET {setters},updated_at=CURRENT_TIMESTAMP WHERE provider=?",
                [*fields.values(), str(provider)],
            )
            await conn.commit()
        settings = await self.get_settings(provider)
        if not settings:
            raise PaygError("PAYG settings row missing")
        if settings["enabled"]:
            if int(settings["price_per_gb_toman"] or 0) <= 0:
                raise PaygError("PAYG rate must be positive before enabling")
            parse_service_ids(str(settings["service_ids"] or ""))
        return settings

    async def set_enabled(self, provider: str, enabled: bool) -> dict[str, Any]:
        if enabled:
            settings = await self.get_settings(provider)
            if not settings or int(settings["price_per_gb_toman"] or 0) <= 0:
                raise PaygError("ابتدا نرخ هر گیگ را تنظیم کنید.")
            try:
                parse_service_ids(str(settings["service_ids"] or ""))
            except ValueError as exc:
                raise PaygError("ابتدا Service IDهای PAYG را تنظیم کنید.") from exc
        return await self.update_settings(provider, enabled=enabled)

    # ------------------------------------------------------------------
    # Wallet + ledger
    # ------------------------------------------------------------------

    async def get_balance(self, user_id: int) -> int:
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            async with conn.execute(
                "SELECT balance_toman FROM wallets WHERE user_id=?", (int(user_id),)
            ) as cur:
                row = await cur.fetchone()
        return int(row[0]) if row else 0

    async def wallet_history(self, user_id: int, limit: int = 20) -> list[dict[str, Any]]:
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                "SELECT * FROM wallet_ledger WHERE user_id=? ORDER BY id DESC LIMIT ?",
                (int(user_id), max(1, min(int(limit), 100))),
            ) as cur:
                rows = await cur.fetchall()
        return [dict(row) for row in rows]

    async def credit_wallet(
        self,
        user_id: int,
        amount_toman: int,
        *,
        kind: str,
        reference_type: str | None = None,
        reference_id: int | None = None,
        note: str | None = None,
    ) -> int:
        amount = int(amount_toman)
        if amount <= 0:
            raise PaygError("wallet credit must be positive")
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute("BEGIN IMMEDIATE")
            balance = await self._balance_in_conn(conn, int(user_id))
            new_balance = balance + amount
            await self._write_balance_in_conn(conn, int(user_id), new_balance)
            await conn.execute(
                """
                INSERT INTO wallet_ledger(
                    user_id,amount_toman,balance_after_toman,kind,
                    reference_type,reference_id,note
                ) VALUES(?,?,?,?,?,?,?)
                """,
                (int(user_id), amount, new_balance, str(kind), reference_type, reference_id, note),
            )
            await conn.commit()
        return new_balance

    async def debit_wallet(
        self,
        user_id: int,
        amount_toman: int,
        *,
        kind: str,
        reference_type: str | None = None,
        reference_id: int | None = None,
        note: str | None = None,
    ) -> int:
        amount = int(amount_toman)
        if amount <= 0:
            raise PaygError("wallet debit must be positive")
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute("BEGIN IMMEDIATE")
            balance = await self._balance_in_conn(conn, int(user_id))
            if balance < amount:
                await conn.rollback()
                raise PaygInsufficientFunds(amount - balance)
            new_balance = balance - amount
            await self._write_balance_in_conn(conn, int(user_id), new_balance)
            await conn.execute(
                """
                INSERT INTO wallet_ledger(
                    user_id,amount_toman,balance_after_toman,kind,
                    reference_type,reference_id,note
                ) VALUES(?,?,?,?,?,?,?)
                """,
                (int(user_id), -amount, new_balance, str(kind), reference_type, reference_id, note),
            )
            await conn.commit()
        return new_balance

    @staticmethod
    async def _balance_in_conn(conn: aiosqlite.Connection, user_id: int) -> int:
        async with conn.execute(
            "SELECT balance_toman FROM wallets WHERE user_id=?", (int(user_id),)
        ) as cur:
            row = await cur.fetchone()
        return int(row[0]) if row else 0

    @staticmethod
    async def _write_balance_in_conn(conn: aiosqlite.Connection, user_id: int, balance: int) -> None:
        await conn.execute(
            """
            INSERT INTO wallets(user_id,balance_toman,updated_at)
            VALUES(?,?,CURRENT_TIMESTAMP)
            ON CONFLICT(user_id) DO UPDATE SET
                balance_toman=excluded.balance_toman,
                updated_at=CURRENT_TIMESTAMP
            """,
            (int(user_id), int(balance)),
        )

    # ------------------------------------------------------------------
    # Top-ups
    # ------------------------------------------------------------------

    async def create_topup(
        self,
        user_id: int,
        amount_toman: int,
        *,
        provider: str = "rebecca",
        purpose: str = "wallet",
        account_id: int | None = None,
        migration_id: int | None = None,
    ) -> int:
        amount = int(amount_toman)
        if amount <= 0:
            raise PaygError("top-up amount must be positive")
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            cur = await conn.execute(
                """
                INSERT INTO payg_topups(
                    user_id,amount_toman,provider,purpose,account_id,migration_id
                ) VALUES(?,?,?,?,?,?)
                """,
                (int(user_id), amount, str(provider), str(purpose), account_id, migration_id),
            )
            await conn.commit()
            return int(cur.lastrowid)

    async def get_topup(self, topup_id: int) -> dict[str, Any] | None:
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute("SELECT * FROM payg_topups WHERE id=?", (int(topup_id),)) as cur:
                row = await cur.fetchone()
        return dict(row) if row else None

    async def submit_topup_receipt(self, topup_id: int, user_id: int, file_id: str) -> bool:
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            cur = await conn.execute(
                """
                UPDATE payg_topups
                SET receipt_file_id=?,status='submitted',updated_at=CURRENT_TIMESTAMP
                WHERE id=? AND user_id=? AND status='pending'
                """,
                (str(file_id), int(topup_id), int(user_id)),
            )
            await conn.commit()
            return cur.rowcount == 1

    async def approve_topup(self, topup_id: int, actor_id: int) -> dict[str, Any]:
        """Atomically approve once and credit the wallet exactly once."""
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            await conn.execute("BEGIN IMMEDIATE")
            async with conn.execute("SELECT * FROM payg_topups WHERE id=?", (int(topup_id),)) as cur:
                row = await cur.fetchone()
            if not row:
                await conn.rollback()
                raise PaygError("top-up not found")
            topup = dict(row)
            if topup["status"] == "approved":
                await conn.rollback()
                return topup
            if topup["status"] != "submitted":
                await conn.rollback()
                raise PaygError("top-up is not awaiting approval")
            user_id = int(topup["user_id"])
            amount = int(topup["amount_toman"])
            balance = await self._balance_in_conn(conn, user_id)
            new_balance = balance + amount
            await self._write_balance_in_conn(conn, user_id, new_balance)
            await conn.execute(
                """
                INSERT INTO wallet_ledger(
                    user_id,amount_toman,balance_after_toman,kind,
                    reference_type,reference_id,note
                ) VALUES(?,?,?,?,?,?,?)
                """,
                (user_id, amount, new_balance, "topup", "payg_topup", int(topup_id), f"purpose={topup['purpose']}"),
            )
            await conn.execute(
                """
                UPDATE payg_topups SET status='approved',approved_by=?,updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (int(actor_id), int(topup_id)),
            )
            await conn.commit()
        return (await self.get_topup(topup_id)) or topup

    async def reject_topup(self, topup_id: int, actor_id: int) -> bool:
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            cur = await conn.execute(
                """
                UPDATE payg_topups SET status='rejected',approved_by=?,updated_at=CURRENT_TIMESTAMP
                WHERE id=? AND status IN ('pending','submitted')
                """,
                (int(actor_id), int(topup_id)),
            )
            await conn.commit()
            return cur.rowcount == 1

    # ------------------------------------------------------------------
    # PAYG accounts + billing
    # ------------------------------------------------------------------

    async def get_account(self, account_id: int) -> dict[str, Any] | None:
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute("SELECT * FROM payg_accounts WHERE id=?", (int(account_id),)) as cur:
                row = await cur.fetchone()
        return dict(row) if row else None

    async def get_user_account(
        self, user_id: int, provider: str = "rebecca", *, include_migrated: bool = False
    ) -> dict[str, Any] | None:
        await self.ensure_schema()
        where = "user_id=? AND provider=?"
        params: list[Any] = [int(user_id), str(provider)]
        if not include_migrated:
            where += " AND status!='migrated'"
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                f"SELECT * FROM payg_accounts WHERE {where} ORDER BY id DESC LIMIT 1", params
            ) as cur:
                row = await cur.fetchone()
        return dict(row) if row else None

    async def account_snapshot(self, user_id: int, provider: str = "rebecca") -> dict[str, Any]:
        settings = await self.get_settings(provider)
        account = await self.get_user_account(user_id, provider)
        balance = await self.get_balance(user_id)
        rate = int((account or settings or {}).get("price_per_gb_toman") or 0)
        outstanding = 0
        if account and rate > 0:
            target = int(account["cumulative_usage_bytes"]) * rate // GIB
            outstanding = max(0, target - int(account["charged_toman_total"]))
        return {
            "settings": settings,
            "account": account,
            "balance_toman": balance,
            "rate_toman": rate,
            "equivalent_gb": (balance / rate) if rate > 0 else 0.0,
            "outstanding_toman": outstanding,
        }

    async def _unique_rebecca_username(self, base: str) -> str:
        clean = "".join(ch for ch in str(base).lower() if ch.isascii() and (ch.isalnum() or ch == "_"))
        clean = clean[:24] or "panel"
        for suffix in range(0, 200):
            candidate = clean if suffix == 0 else f"{clean}_{suffix}"
            remote = await rebecca_api.find_admin(candidate)
            if remote is None:
                return candidate
        raise PaygError("could not allocate a unique Rebecca username")

    async def ensure_payg_account(self, user_id: int, provider: str = "rebecca") -> dict[str, Any]:
        existing = await self.get_user_account(user_id, provider)
        if existing:
            return existing
        if provider != "rebecca":
            raise PaygError("Sanaei PAYG provisioning is not implemented yet")
        settings = await self.get_settings(provider)
        if not settings or not int(settings["enabled"]):
            raise PaygError("PAYG is disabled")
        rate = int(settings["price_per_gb_toman"] or 0)
        minimum = int(settings["min_topup_toman"] or 0)
        if rate <= 0:
            raise PaygError("PAYG rate is not configured")
        service_ids = parse_service_ids(str(settings["service_ids"] or ""))
        balance = await self.get_balance(user_id)
        if balance < minimum:
            raise PaygInsufficientFunds(minimum - balance)

        username = await self._unique_rebecca_username(f"payg{int(user_id)}")
        password = secrets.token_urlsafe(10)
        created_remote = False
        try:
            await rebecca_api.create_admin_verified(
                username,
                password,
                int(user_id),
                data_limit=None,
                expire=None,
                users_limit=int(settings["max_users"]),
                services=service_ids,
            )
            created_remote = True
            baseline = await rebecca_api.get_admin_usage(username)
            admin_model = AdminModel(
                user_id=int(user_id),
                admin_name=str(settings["display_name"] or "PAYG Rebecca"),
                marzban_username=username,
                marzban_password=password,
                max_users=int(settings["max_users"]),
                max_total_time=_LONG_VALIDITY_DAYS * 86400,
                max_total_traffic=0,
                validity_days=_LONG_VALIDITY_DAYS,
                is_active=True,
                origin_plan_id=None,
                allow_incremental_renewal=False,
            )
            if not await db.add_admin(admin_model):
                raise PaygError("failed to save PAYG admin locally")
            local = await db.get_admin_by_marzban_username(username)
            if not local or local.id is None:
                raise PaygError("PAYG admin local lookup failed")
            async with aiosqlite.connect(self.db_path) as conn:
                cur = await conn.execute(
                    """
                    INSERT INTO payg_accounts(
                        user_id,admin_id,provider,status,price_per_gb_toman,last_raw_usage_bytes
                    ) VALUES(?,?,?,?,?,?)
                    """,
                    (int(user_id), int(local.id), provider, "active", rate, int(baseline)),
                )
                await conn.commit()
                account_id = int(cur.lastrowid)
            account = await self.get_account(account_id)
            if not account:
                raise PaygError("PAYG account creation failed")
            return account
        except Exception:
            if created_remote:
                try:
                    await rebecca_api.delete_admin(username)
                except Exception:
                    pass
            local = await db.get_admin_by_marzban_username(username)
            if local and local.id:
                try:
                    await db.remove_admin_by_id(int(local.id))
                except Exception:
                    pass
            raise

    async def settle_usage(self, account_id: int, raw_usage_bytes: int) -> BillingResult:
        raw = max(0, int(raw_usage_bytes))
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            await conn.execute("BEGIN IMMEDIATE")
            async with conn.execute("SELECT * FROM payg_accounts WHERE id=?", (int(account_id),)) as cur:
                row = await cur.fetchone()
            if not row:
                await conn.rollback()
                raise PaygError("PAYG account not found")
            account = dict(row)
            old_status = str(account["status"])
            if old_status == "migrated":
                await conn.rollback()
                return BillingResult(
                    int(account_id), int(account["user_id"]), old_status, old_status,
                    raw, int(account["cumulative_usage_bytes"]), 0,
                    int(account["charged_toman_total"]), 0,
                    await self.get_balance(int(account["user_id"])),
                )
            last_raw = int(account["last_raw_usage_bytes"] or 0)
            delta_bytes = (raw - last_raw) if raw >= last_raw else raw
            cumulative = int(account["cumulative_usage_bytes"] or 0) + max(0, delta_bytes)
            rate = int(account["price_per_gb_toman"])
            target_charge = cumulative * rate // GIB
            charged_total = int(account["charged_toman_total"] or 0)
            due = max(0, target_charge - charged_total)
            user_id = int(account["user_id"])
            balance = await self._balance_in_conn(conn, user_id)
            charged_now = min(balance, due)
            new_balance = balance - charged_now
            new_charged_total = charged_total + charged_now
            outstanding = max(0, target_charge - new_charged_total)
            new_status = "active" if outstanding == 0 and new_balance > 0 else "suspended"

            if charged_now:
                await self._write_balance_in_conn(conn, user_id, new_balance)
                await conn.execute(
                    """
                    INSERT INTO wallet_ledger(
                        user_id,amount_toman,balance_after_toman,kind,
                        reference_type,reference_id,note
                    ) VALUES(?,?,?,?,?,?,?)
                    """,
                    (
                        user_id, -charged_now, new_balance, "payg_usage",
                        "payg_account", int(account_id), f"usage_bytes={cumulative}",
                    ),
                )
            await conn.execute(
                """
                UPDATE payg_accounts SET
                    last_raw_usage_bytes=?,cumulative_usage_bytes=?,charged_toman_total=?,
                    status=?,updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (raw, cumulative, new_charged_total, new_status, int(account_id)),
            )
            await conn.commit()

        return BillingResult(
            account_id=int(account_id), user_id=user_id, old_status=old_status,
            new_status=new_status, raw_usage_bytes=raw,
            cumulative_usage_bytes=cumulative, charged_now_toman=charged_now,
            charged_total_toman=new_charged_total, outstanding_toman=outstanding,
            balance_toman=new_balance,
        )

    async def sync_account(self, account_id: int) -> BillingResult:
        account = await self.get_account(account_id)
        if not account:
            raise PaygError("PAYG account not found")
        if account["status"] == "migrated":
            return await self.settle_usage(account_id, int(account["last_raw_usage_bytes"] or 0))
        admin = await db.get_admin_by_id(int(account["admin_id"]))
        if not admin or not admin.marzban_username:
            raise PaygError("PAYG admin is missing")
        if account["provider"] != "rebecca":
            raise PaygError("PAYG billing provider is not implemented")
        raw = await rebecca_api.get_admin_usage(admin.marzban_username)
        result = await self.settle_usage(account_id, raw)

        if result.new_status == "suspended" and result.old_status != "suspended":
            try:
                await rebecca_api.disable_admin(admin.marzban_username, "PAYG balance exhausted")
            finally:
                await db.deactivate_admin(int(admin.id), "PAYG balance exhausted")
        elif result.new_status == "active" and result.old_status == "suspended":
            try:
                await rebecca_api.enable_admin(admin.marzban_username)
            finally:
                await db.reactivate_admin(int(admin.id))
        return result

    async def sync_all_accounts(self) -> list[BillingResult]:
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            async with conn.execute(
                "SELECT id FROM payg_accounts WHERE status IN ('active','suspended') ORDER BY id"
            ) as cur:
                ids = [int(row[0]) for row in await cur.fetchall()]
        results: list[BillingResult] = []
        for account_id in ids:
            try:
                results.append(await self.sync_account(account_id))
            except Exception as exc:
                print(f"PAYG billing sync failed for account {account_id}: {type(exc).__name__}: {exc}")
        return results

    # ------------------------------------------------------------------
    # Migration PAYG -> fixed plan
    # ------------------------------------------------------------------

    async def list_migration_plans(self) -> list[Any]:
        plans = await db.get_plans(only_active=True)
        return [
            plan for plan in plans
            if int(getattr(plan, "price", 0) or 0) > 0
            and str(getattr(plan, "rebecca_service_ids", "") or "").strip()
        ]

    async def get_migration(self, migration_id: int) -> dict[str, Any] | None:
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                "SELECT * FROM payg_migrations WHERE id=?", (int(migration_id),)
            ) as cur:
                row = await cur.fetchone()
        return dict(row) if row else None

    async def prepare_migration(
        self, user_id: int, account_id: int, target_plan_id: int
    ) -> tuple[dict[str, Any], int | None]:
        account = await self.get_account(account_id)
        if not account or int(account["user_id"]) != int(user_id):
            raise PaygError("PAYG account not found")
        if account["status"] not in {"active", "suspended"}:
            raise PaygError("PAYG account cannot be migrated")
        plan = await db.get_plan_by_id(int(target_plan_id))
        if not plan or not bool(getattr(plan, "is_active", True)):
            raise PaygError("target plan not found")
        price = int(getattr(plan, "price", 0) or 0)
        if price <= 0:
            raise PaygError("target plan price is invalid")
        parse_service_ids(str(getattr(plan, "rebecca_service_ids", "") or ""))
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            await conn.execute("BEGIN IMMEDIATE")
            async with conn.execute(
                """
                SELECT * FROM payg_migrations
                WHERE account_id=? AND status IN ('waiting_funds','provisioning')
                ORDER BY id DESC LIMIT 1
                """,
                (int(account_id),),
            ) as cur:
                existing = await cur.fetchone()
            if existing:
                migration = dict(existing)
            else:
                cur = await conn.execute(
                    """
                    INSERT INTO payg_migrations(
                        user_id,account_id,target_plan_id,price_toman,status
                    ) VALUES(?,?,?,?, 'waiting_funds')
                    """,
                    (int(user_id), int(account_id), int(target_plan_id), price),
                )
                migration_id = int(cur.lastrowid)
                migration = {
                    "id": migration_id,
                    "user_id": int(user_id),
                    "account_id": int(account_id),
                    "target_plan_id": int(target_plan_id),
                    "price_toman": price,
                    "status": "waiting_funds",
                }
            await conn.commit()

        balance = await self.get_balance(user_id)
        shortfall = max(0, int(migration["price_toman"]) - balance)
        topup_id: int | None = None
        if shortfall > 0:
            # Reuse an existing still-payable top-up for this migration.
            async with aiosqlite.connect(self.db_path) as conn:
                async with conn.execute(
                    """
                    SELECT id FROM payg_topups
                    WHERE migration_id=? AND status IN ('pending','submitted')
                    ORDER BY id DESC LIMIT 1
                    """,
                    (int(migration["id"]),),
                ) as cur:
                    row = await cur.fetchone()
            topup_id = int(row[0]) if row else await self.create_topup(
                user_id,
                shortfall,
                provider=str(account["provider"]),
                purpose="migration",
                account_id=int(account_id),
                migration_id=int(migration["id"]),
            )
        return (await self.get_migration(int(migration["id"]))) or migration, topup_id

    async def execute_migration(self, migration_id: int) -> MigrationResult:
        migration = await self.get_migration(migration_id)
        if not migration:
            raise PaygError("migration not found")
        if migration["status"] == "completed":
            admin = await db.get_admin_by_id(int(migration["new_admin_id"])) if migration.get("new_admin_id") else None
            plan = await db.get_plan_by_id(int(migration["target_plan_id"]))
            if not admin or not plan:
                raise PaygError("completed migration is inconsistent")
            return MigrationResult(
                int(migration_id), int(admin.id), str(admin.marzban_username), str(admin.marzban_password),
                str(admin.login_url or config.REBECCA_LOGIN_URL or config.REBECCA_URL), str(plan.name),
            )
        account = await self.get_account(int(migration["account_id"]))
        if not account or int(account["user_id"]) != int(migration["user_id"]):
            raise PaygError("migration account is invalid")
        if account["provider"] != "rebecca":
            raise PaygError("Sanaei migration is not implemented yet")
        plan = await db.get_plan_by_id(int(migration["target_plan_id"]))
        if not plan or not bool(getattr(plan, "is_active", True)):
            raise PaygError("migration target plan is unavailable")
        price = int(migration["price_toman"])
        balance = await self.get_balance(int(migration["user_id"]))
        if balance < price:
            raise PaygInsufficientFunds(price - balance)

        async with aiosqlite.connect(self.db_path) as conn:
            cur = await conn.execute(
                """
                UPDATE payg_migrations SET status='provisioning',error_message=NULL,updated_at=CURRENT_TIMESTAMP
                WHERE id=? AND status IN ('waiting_funds','failed')
                """,
                (int(migration_id),),
            )
            await conn.commit()
            if cur.rowcount != 1:
                raise PaygError("migration is already being processed")

        await self.debit_wallet(
            int(migration["user_id"]),
            price,
            kind="fixed_plan_purchase",
            reference_type="payg_migration",
            reference_id=int(migration_id),
            note=f"plan_id={int(migration['target_plan_id'])}",
        )
        reserved = True
        username = ""
        remote_created = False
        local_admin_id: int | None = None
        try:
            service_ids = parse_service_ids(str(getattr(plan, "rebecca_service_ids", "") or ""))
            username = await self._unique_rebecca_username(f"panel{int(migration['user_id'])}")
            password = secrets.token_urlsafe(10)
            now = int(time.time())
            ttl = getattr(plan, "time_limit_seconds", None)
            expire = None if ttl is None else now + int(ttl)
            data_limit = getattr(plan, "traffic_limit_bytes", None)
            users_limit = getattr(plan, "max_users", None)
            await rebecca_api.create_admin_verified(
                username,
                password,
                int(migration["user_id"]),
                data_limit=data_limit,
                expire=expire,
                users_limit=users_limit,
                services=service_ids,
            )
            remote_created = True
            validity_days = _LONG_VALIDITY_DAYS if ttl is None else max(1, int(ttl) // 86400)
            admin_model = AdminModel(
                user_id=int(migration["user_id"]),
                admin_name=str(plan.name),
                marzban_username=username,
                marzban_password=password,
                login_url=(config.REBECCA_LOGIN_URL or config.REBECCA_URL),
                max_users=(int(users_limit) if users_limit is not None else 1000000),
                max_total_time=(int(ttl) if ttl is not None else _LONG_VALIDITY_DAYS * 86400),
                max_total_traffic=(int(data_limit) if data_limit is not None else 0),
                validity_days=validity_days,
                is_active=True,
                origin_plan_id=int(plan.id),
                allow_incremental_renewal=bool(getattr(plan, "allow_incremental_renewal", True)),
            )
            if not await db.add_admin(admin_model):
                raise PaygError("failed to save migrated fixed panel")
            local = await db.get_admin_by_marzban_username(username)
            if not local or local.id is None:
                raise PaygError("migrated panel local lookup failed")
            local_admin_id = int(local.id)

            old_admin = await db.get_admin_by_id(int(account["admin_id"]))
            if old_admin and old_admin.marzban_username:
                try:
                    remote = await rebecca_api.find_admin(old_admin.marzban_username)
                    if remote and str(remote.get("status", "")).lower() == "active":
                        await rebecca_api.disable_admin(old_admin.marzban_username, "Migrated to fixed plan")
                finally:
                    if old_admin.id:
                        await db.deactivate_admin(int(old_admin.id), "Migrated to fixed plan")

            async with aiosqlite.connect(self.db_path) as conn:
                await conn.execute("BEGIN IMMEDIATE")
                await conn.execute(
                    "UPDATE payg_accounts SET status='migrated',updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (int(account["id"]),),
                )
                await conn.execute(
                    """
                    UPDATE payg_migrations SET status='completed',new_admin_id=?,error_message=NULL,
                        updated_at=CURRENT_TIMESTAMP WHERE id=?
                    """,
                    (local_admin_id, int(migration_id)),
                )
                await conn.commit()
            reserved = False
            return MigrationResult(
                migration_id=int(migration_id),
                new_admin_id=local_admin_id,
                username=username,
                password=password,
                login_url=(config.REBECCA_LOGIN_URL or config.REBECCA_URL),
                plan_name=str(plan.name),
            )
        except Exception as exc:
            if local_admin_id:
                try:
                    await db.remove_admin_by_id(local_admin_id)
                except Exception:
                    pass
            if remote_created and username:
                try:
                    await rebecca_api.delete_admin(username)
                except Exception:
                    pass
            if reserved:
                try:
                    await self.credit_wallet(
                        int(migration["user_id"]),
                        price,
                        kind="migration_refund",
                        reference_type="payg_migration",
                        reference_id=int(migration_id),
                        note="provisioning failed; automatic refund",
                    )
                except Exception:
                    pass
            async with aiosqlite.connect(self.db_path) as conn:
                await conn.execute(
                    """
                    UPDATE payg_migrations SET status='failed',error_message=?,updated_at=CURRENT_TIMESTAMP
                    WHERE id=?
                    """,
                    (f"{type(exc).__name__}: {exc}"[:500], int(migration_id)),
                )
                await conn.commit()
            raise

    async def post_topup_approval(self, topup_id: int) -> dict[str, Any]:
        """Continue the flow after a verified payment without crediting twice."""
        topup = await self.get_topup(topup_id)
        if not topup or topup["status"] != "approved":
            raise PaygError("top-up is not approved")
        result: dict[str, Any] = {"topup": topup}
        purpose = str(topup["purpose"])
        if purpose == "initial":
            result["account"] = await self.ensure_payg_account(
                int(topup["user_id"]), str(topup["provider"])
            )
        elif purpose == "migration" and topup.get("migration_id"):
            migration_id = int(topup["migration_id"])
            migration = await self.get_migration(migration_id)
            if migration:
                balance = await self.get_balance(int(topup["user_id"]))
                if balance >= int(migration["price_toman"]):
                    result["migration"] = await self.execute_migration(migration_id)
        elif topup.get("account_id"):
            result["billing"] = await self.sync_account(int(topup["account_id"]))
        return result


payg_service = PaygService()
