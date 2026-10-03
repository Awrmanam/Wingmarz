from __future__ import annotations

import secrets
from typing import Any

import aiosqlite

import config
from database import db
from models.schemas import AdminModel
from payg_service import GIB, PaygError, PaygInsufficientFunds, payg_service
from rebecca_api import rebecca_api
from rebecca_catalog import get_service_by_rebecca_id, list_services
from utils.rebecca import parse_service_ids


_LONG_VALIDITY_DAYS = 36500


class PaygOfferError(PaygError):
    pass


class PaygOfferService:
    """Service-specific PAYG pricing layered on the shared wallet/billing core.

    Rebecca service IDs come from the existing service catalog.  Pricing is no
    longer provider-wide: every service can have its own rate, minimum top-up
    and user limit.  Existing provider-wide settings are migrated once with
    INSERT OR IGNORE so current installations keep working after upgrade.
    """

    def __init__(self, db_path: str | None = None) -> None:
        self._db_path = db_path

    @property
    def db_path(self) -> str:
        return self._db_path or config.DATABASE_PATH

    async def ensure_schema(self) -> None:
        await payg_service.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS payg_service_offers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    provider TEXT NOT NULL DEFAULT 'rebecca',
                    service_id INTEGER NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 0,
                    price_per_gb_toman INTEGER NOT NULL DEFAULT 0,
                    min_topup_toman INTEGER NOT NULL DEFAULT 100000,
                    max_users INTEGER NOT NULL DEFAULT 50,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(provider, service_id)
                );
                CREATE INDEX IF NOT EXISTS idx_payg_service_offers_enabled
                    ON payg_service_offers(provider, enabled, service_id);

                CREATE TABLE IF NOT EXISTS payg_account_services (
                    account_id INTEGER PRIMARY KEY,
                    provider TEXT NOT NULL DEFAULT 'rebecca',
                    service_id INTEGER NOT NULL,
                    display_name_snapshot TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_payg_account_services_service
                    ON payg_account_services(provider, service_id, account_id);

                CREATE TABLE IF NOT EXISTS payg_service_topups (
                    topup_id INTEGER PRIMARY KEY,
                    service_id INTEGER NOT NULL,
                    source TEXT NOT NULL DEFAULT 'p',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                """
            )

            # Migrate the old provider-wide PAYG row to one offer per configured
            # service. Existing per-service rows always win and are never
            # overwritten by this compatibility migration.
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                "SELECT * FROM payg_settings WHERE provider='rebecca'"
            ) as cur:
                legacy = await cur.fetchone()
            legacy_ids: list[int] = []
            if legacy:
                raw_ids = str(legacy["service_ids"] or "").strip()
                if raw_ids:
                    try:
                        legacy_ids = parse_service_ids(raw_ids)
                    except ValueError:
                        legacy_ids = []
                for service_id in legacy_ids:
                    await conn.execute(
                        """
                        INSERT OR IGNORE INTO payg_service_offers(
                            provider,service_id,enabled,price_per_gb_toman,
                            min_topup_toman,max_users
                        ) VALUES('rebecca',?,?,?,?,?)
                        """,
                        (
                            int(service_id),
                            int(legacy["enabled"] or 0),
                            int(legacy["price_per_gb_toman"] or 0),
                            int(legacy["min_topup_toman"] or 100000),
                            int(legacy["max_users"] or 50),
                        ),
                    )

                # A legacy account did not carry its service ID. If the legacy
                # installation had exactly one PAYG service, map old accounts
                # safely to that service so their UI remains reachable.
                if len(legacy_ids) == 1:
                    await conn.execute(
                        """
                        INSERT OR IGNORE INTO payg_account_services(account_id,provider,service_id)
                        SELECT id,'rebecca',?
                        FROM payg_accounts
                        WHERE provider='rebecca'
                        """,
                        (int(legacy_ids[0]),),
                    )
            await conn.commit()

    async def get_offer(self, service_id: int, provider: str = "rebecca") -> dict[str, Any] | None:
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                "SELECT * FROM payg_service_offers WHERE provider=? AND service_id=?",
                (str(provider), int(service_id)),
            ) as cur:
                row = await cur.fetchone()
        return dict(row) if row else None

    async def ensure_offer(self, service_id: int, provider: str = "rebecca") -> dict[str, Any]:
        if provider != "rebecca":
            raise PaygOfferError("Sanaei PAYG offers are not implemented yet")
        service = await get_service_by_rebecca_id(int(service_id))
        if not service:
            raise PaygOfferError("ابتدا این سرویس را در کاتالوگ Rebecca ثبت کنید.")
        await self.ensure_schema()
        legacy = await payg_service.get_settings(provider)
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                """
                INSERT OR IGNORE INTO payg_service_offers(
                    provider,service_id,enabled,price_per_gb_toman,min_topup_toman,max_users
                ) VALUES(?,?,0,?,?,?)
                """,
                (
                    provider,
                    int(service_id),
                    int((legacy or {}).get("price_per_gb_toman") or 0),
                    int((legacy or {}).get("min_topup_toman") or 100000),
                    int((legacy or {}).get("max_users") or 50),
                ),
            )
            await conn.commit()
        offer = await self.get_offer(service_id, provider)
        if not offer:
            raise PaygOfferError("PAYG offer row missing")
        return offer

    async def update_offer(self, service_id: int, provider: str = "rebecca", **fields: Any) -> dict[str, Any]:
        allowed = {"enabled", "price_per_gb_toman", "min_topup_toman", "max_users"}
        if not fields or any(key not in allowed for key in fields):
            raise PaygOfferError("invalid PAYG offer update")
        offer = await self.ensure_offer(service_id, provider)
        if "enabled" in fields:
            fields["enabled"] = 1 if bool(fields["enabled"]) else 0
        for key in ("price_per_gb_toman", "min_topup_toman", "max_users"):
            if key in fields:
                fields[key] = int(fields[key])
                if fields[key] < 1:
                    raise PaygOfferError(f"invalid {key}")
        if fields.get("enabled"):
            rate = int(fields.get("price_per_gb_toman", offer["price_per_gb_toman"]) or 0)
            if rate <= 0:
                raise PaygOfferError("ابتدا نرخ هر گیگ این سرویس را تنظیم کنید.")
        setters = ",".join(f"{key}=?" for key in fields)
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                f"UPDATE payg_service_offers SET {setters},updated_at=CURRENT_TIMESTAMP "
                "WHERE provider=? AND service_id=?",
                [*fields.values(), provider, int(service_id)],
            )
            await conn.commit()
        updated = await self.get_offer(service_id, provider)
        if not updated:
            raise PaygOfferError("PAYG offer row missing")
        return updated

    async def list_offers(self, provider: str = "rebecca", *, enabled_only: bool = False) -> list[dict[str, Any]]:
        await self.ensure_schema()
        sql = "SELECT * FROM payg_service_offers WHERE provider=?"
        params: list[Any] = [str(provider)]
        if enabled_only:
            sql += " AND enabled=1 AND price_per_gb_toman>0"
        sql += " ORDER BY service_id"
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(sql, params) as cur:
                rows = [dict(row) for row in await cur.fetchall()]
        for row in rows:
            service = await get_service_by_rebecca_id(int(row["service_id"]))
            row["catalog_id"] = int(service.id) if service else None
            row["display_name"] = str(service.display_name) if service else f"Service #{row['service_id']}"
            row["catalog_enabled"] = bool(service and service.is_enabled)
        if enabled_only:
            rows = [row for row in rows if row["catalog_enabled"]]
        return rows

    async def list_catalog_with_offers(self) -> list[tuple[Any, dict[str, Any] | None]]:
        services = await list_services(enabled_only=False)
        offers = {int(item["service_id"]): item for item in await self.list_offers()}
        return [(service, offers.get(int(service.rebecca_service_id))) for service in services]

    async def account_for_user_service(
        self, user_id: int, service_id: int, provider: str = "rebecca"
    ) -> dict[str, Any] | None:
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                """
                SELECT a.* FROM payg_accounts a
                JOIN payg_account_services s ON s.account_id=a.id
                WHERE a.user_id=? AND a.provider=? AND s.service_id=? AND a.status!='migrated'
                ORDER BY a.id DESC LIMIT 1
                """,
                (int(user_id), str(provider), int(service_id)),
            ) as cur:
                row = await cur.fetchone()
        return dict(row) if row else None

    async def snapshot(self, user_id: int, service_id: int) -> dict[str, Any]:
        offer = await self.get_offer(service_id)
        account = await self.account_for_user_service(user_id, service_id)
        balance = await payg_service.get_balance(int(user_id))
        rate = int((account or offer or {}).get("price_per_gb_toman") or 0)
        outstanding = 0
        if account and rate > 0:
            target = int(account["cumulative_usage_bytes"] or 0) * rate // GIB
            outstanding = max(0, target - int(account["charged_toman_total"] or 0))
        return {
            "offer": offer,
            "account": account,
            "balance_toman": balance,
            "rate_toman": rate,
            "equivalent_gb": balance / rate if rate > 0 else 0.0,
            "outstanding_toman": outstanding,
        }

    async def _unique_username(self, user_id: int, service_id: int) -> str:
        base = f"payg{int(user_id)}_{int(service_id)}"[:24]
        for suffix in range(200):
            candidate = base if suffix == 0 else f"{base[:20]}_{suffix}"
            if await rebecca_api.find_admin(candidate) is None:
                return candidate
        raise PaygOfferError("could not allocate a unique Rebecca username")

    async def provision(self, user_id: int, service_id: int) -> dict[str, Any]:
        existing = await self.account_for_user_service(user_id, service_id)
        if existing:
            return existing
        offer = await self.get_offer(service_id)
        if not offer or not int(offer["enabled"]):
            raise PaygOfferError("PAYG این سرویس فعال نیست.")
        service = await get_service_by_rebecca_id(int(service_id))
        if not service or not service.is_enabled:
            raise PaygOfferError("سرویس Rebecca فعال نیست.")
        rate = int(offer["price_per_gb_toman"] or 0)
        minimum = int(offer["min_topup_toman"] or 0)
        if rate <= 0:
            raise PaygOfferError("نرخ PAYG این سرویس تنظیم نشده است.")
        balance = await payg_service.get_balance(int(user_id))
        if balance < minimum:
            raise PaygInsufficientFunds(minimum - balance)

        username = await self._unique_username(user_id, service_id)
        password = secrets.token_urlsafe(10)
        remote_created = False
        local_admin_id: int | None = None
        try:
            await rebecca_api.create_admin_verified(
                username,
                password,
                int(user_id),
                data_limit=None,
                expire=None,
                users_limit=int(offer["max_users"]),
                services=[int(service_id)],
            )
            remote_created = True
            baseline = await rebecca_api.get_admin_usage(username)
            admin_model = AdminModel(
                user_id=int(user_id),
                admin_name=f"PAYG {service.display_name}",
                marzban_username=username,
                marzban_password=password,
                max_users=int(offer["max_users"]),
                max_total_time=_LONG_VALIDITY_DAYS * 86400,
                max_total_traffic=0,
                validity_days=_LONG_VALIDITY_DAYS,
                is_active=True,
                origin_plan_id=None,
                allow_incremental_renewal=False,
            )
            if not await db.add_admin(admin_model):
                raise PaygOfferError("failed to save PAYG admin locally")
            local = await db.get_admin_by_marzban_username(username)
            if not local or local.id is None:
                raise PaygOfferError("PAYG admin local lookup failed")
            local_admin_id = int(local.id)
            async with aiosqlite.connect(self.db_path) as conn:
                await conn.execute("BEGIN IMMEDIATE")
                cur = await conn.execute(
                    """
                    INSERT INTO payg_accounts(
                        user_id,admin_id,provider,status,price_per_gb_toman,last_raw_usage_bytes
                    ) VALUES(?,?,?,?,?,?)
                    """,
                    (int(user_id), local_admin_id, "rebecca", "active", rate, int(baseline)),
                )
                account_id = int(cur.lastrowid)
                await conn.execute(
                    """
                    INSERT INTO payg_account_services(account_id,provider,service_id,display_name_snapshot)
                    VALUES(?,?,?,?)
                    """,
                    (account_id, "rebecca", int(service_id), str(service.display_name)),
                )
                await conn.commit()
            account = await payg_service.get_account(account_id)
            if not account:
                raise PaygOfferError("PAYG account creation failed")
            return account
        except Exception:
            if local_admin_id:
                try:
                    await db.remove_admin_by_id(local_admin_id)
                except Exception:
                    pass
            if remote_created:
                try:
                    await rebecca_api.delete_admin(username)
                except Exception:
                    pass
            raise

    async def create_topup(
        self,
        user_id: int,
        service_id: int,
        amount_toman: int,
        *,
        source: str,
        account_id: int | None = None,
    ) -> int:
        offer = await self.get_offer(service_id)
        if not offer or not int(offer["enabled"]):
            raise PaygOfferError("PAYG این سرویس فعال نیست.")
        amount = int(amount_toman)
        if amount < int(offer["min_topup_toman"]):
            raise PaygOfferError("مبلغ شارژ کمتر از حداقل این سرویس است.")
        purpose = "wallet" if account_id else "initial_service"
        topup_id = await payg_service.create_topup(
            int(user_id),
            amount,
            provider="rebecca",
            purpose=purpose,
            account_id=account_id,
        )
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                "INSERT OR REPLACE INTO payg_service_topups(topup_id,service_id,source) VALUES(?,?,?)",
                (int(topup_id), int(service_id), str(source)),
            )
            await conn.commit()
        return topup_id

    async def topup_context(self, topup_id: int) -> dict[str, Any] | None:
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                "SELECT * FROM payg_service_topups WHERE topup_id=?", (int(topup_id),)
            ) as cur:
                row = await cur.fetchone()
        return dict(row) if row else None

    async def approve_topup(self, topup_id: int, actor_id: int) -> dict[str, Any]:
        context = await self.topup_context(topup_id)
        if not context:
            raise PaygOfferError("PAYG service top-up context not found")
        topup = await payg_service.approve_topup(int(topup_id), int(actor_id))
        result: dict[str, Any] = {"topup": topup, "service_id": int(context["service_id"])}
        if str(topup["purpose"]) == "initial_service":
            result["account"] = await self.provision(
                int(topup["user_id"]), int(context["service_id"])
            )
        elif topup.get("account_id"):
            result["billing"] = await payg_service.sync_account(int(topup["account_id"]))
        return result


payg_offer_service = PaygOfferService()
