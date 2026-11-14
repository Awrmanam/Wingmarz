from __future__ import annotations

import secrets
import os
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

                CREATE TABLE IF NOT EXISTS payg_provision_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    operation_id INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    detail TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS payg_provision_operations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    provider TEXT NOT NULL DEFAULT 'rebecca',
                    service_id INTEGER NOT NULL,
                    remote_username TEXT NOT NULL UNIQUE,
                    remote_password TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'reserved'
                        CHECK(status IN ('reserved','remote_pending','reconcile','completed')),
                    last_error TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id,provider,service_id)
                );
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
        setters = ",".join(f"{key}=?" for key in fields)
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            await conn.execute("BEGIN IMMEDIATE")
            async with conn.execute(
                "SELECT * FROM payg_service_offers WHERE provider=? AND service_id=?",
                (provider, int(service_id)),
            ) as cur:
                current = await cur.fetchone()
            if current is None:
                await conn.rollback()
                raise PaygOfferError("PAYG offer row missing")
            proposed = {**dict(current), **fields}
            # Updating an already-enabled offer must not permit a zero rate.
            if int(proposed["enabled"]) and int(proposed["price_per_gb_toman"] or 0) <= 0:
                await conn.rollback()
                raise PaygOfferError("ابتدا نرخ هر گیگ این سرویس را تنظیم کنید.")
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
            target = await payg_service.get_accrued_target(int(account["id"]))
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

    async def inspect_incomplete_provisions(self) -> list[dict[str, Any]]:
        """Read-only remote reconciliation; never retry CREATE after ambiguity.

        Safe to invoke on restart. A remote username match is NOT proof of
        ownership, so adoption requires separately verified operator action.
        """
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                "SELECT id,remote_username,status FROM payg_provision_operations "
                "WHERE status IN ('remote_pending','reconcile') ORDER BY id",
            ) as cur:
                rows = [dict(row) for row in await cur.fetchall()]
        results = []
        for row in rows:
            operation_id = int(row["id"])
            try:
                remote = await rebecca_api.find_admin(str(row["remote_username"]))
                result = "remote_present_needs_ownership_review" if remote is not None else "remote_absent_unverified"
            except Exception as exc:
                result = "rebecca_unavailable"
            async with aiosqlite.connect(self.db_path) as conn:
                await conn.execute("BEGIN IMMEDIATE")
                await conn.execute(
                    "UPDATE payg_provision_operations SET status='reconcile',"
                    "last_error=?,updated_at=CURRENT_TIMESTAMP WHERE id=? "
                    "AND status IN ('remote_pending','reconcile')",
                    (result,operation_id),
                )
                await conn.execute(
                    "INSERT INTO payg_provision_audit(operation_id,event_type,detail) "
                    "VALUES(?,'inspection',?)",
                    (operation_id,result),
                )
                await conn.commit()
            results.append({"id":operation_id,"status":result})
        return results

    async def list_pending_provisions(self, actor_id: int) -> list[dict[str, Any]]:
        self._require_owner(actor_id)
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                "SELECT id,user_id,service_id,remote_username,status,last_error "
                "FROM payg_provision_operations WHERE status!='completed' ORDER BY id",
            ) as cur:
                return [dict(row) for row in await cur.fetchall()]

    @staticmethod
    def _require_owner(actor_id: int) -> None:
        owner = os.getenv("PAYG_OWNER_ID", "").strip()
        if not owner.isdecimal() or int(actor_id) != int(owner):
            raise PaygOfferError("owner permission required")

    async def adopt_verified_provision(
        self, operation_id: int, actor_id: int, *, reason: str,
    ) -> dict[str, Any]:
        """Owner-triggered reconciliation of an existing remote identity.

        A matching username alone is insufficient: require a matching remote
        Telegram owner, role, service and a fresh usage counter. No CREATE,
        DELETE or wallet debit occurs. Incomplete remote metadata fails closed.
        """
        self._require_owner(actor_id)
        if len(reason.strip()) < 12:
            raise PaygOfferError("reconciliation reason must have at least 12 characters")
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                "SELECT * FROM payg_provision_operations WHERE id=?",
                (int(operation_id),),
            ) as cur:
                op = await cur.fetchone()
        if op is None or op["status"] == "completed":
            raise PaygOfferError("pending operation not found")
        username = str(op["remote_username"])
        user_id, service_id = int(op["user_id"]), int(op["service_id"])
        remote = await rebecca_api.find_admin(username)
        if not isinstance(remote, dict):
            raise PaygOfferError("remote identity absent or unavailable; manual review required")
        # Do not trust Rebecca's permissive verification for recovery: these
        # fields must actually be present to prove ownership and entitlement.
        if (remote.get("username") != username
            or str(remote.get("role")) != "standard"
            or str(remote.get("status", "")).lower() not in {"active", "disabled"}
            or str(remote.get("telegram_id")) != str(user_id)
            or not isinstance(remote.get("services"), list)
            or service_id not in {
                int(item["id"]) if isinstance(item, dict) else int(item)
                for item in remote["services"]
            }):
            raise PaygOfferError("remote identity ownership or service cannot be verified")
        if "permissions" not in remote:
            raise PaygOfferError("remote permissions are missing; cannot adopt safely")
        from rebecca_api import permissions_are_user_only
        if not permissions_are_user_only(remote["permissions"]):
            raise PaygOfferError("remote permissions exceed user-only access")
        baseline = int(await rebecca_api.get_admin_usage(username))
        if baseline < 0:
            raise PaygOfferError("invalid remote usage")
        offer = await self.get_offer(service_id)
        service = await get_service_by_rebecca_id(service_id)
        if not offer or not int(offer["enabled"]) or not service or not service.is_enabled:
            raise PaygOfferError("service offer is not enabled")
        local = await db.get_admin_by_marzban_username(username)
        if local is not None and int(local.user_id) != user_id:
            raise PaygOfferError("local admin belongs to another customer")
        if local is None:
            model = AdminModel(
                user_id=user_id,admin_name=f"PAYG {service.display_name}",
                marzban_username=username,marzban_password=str(op["remote_password"]),
                max_users=int(offer["max_users"]),
                max_total_time=_LONG_VALIDITY_DAYS * 86400,
                max_total_traffic=0,validity_days=_LONG_VALIDITY_DAYS,
                is_active=True,origin_plan_id=None,
                allow_incremental_renewal=False,
            )
            if not await db.add_admin(model):
                raise PaygOfferError("local admin persistence failed")
            local = await db.get_admin_by_marzban_username(username)
        if local is None or local.id is None or int(local.user_id) != user_id:
            raise PaygOfferError("local admin ownership mismatch")
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            await conn.execute("BEGIN IMMEDIATE")
            async with conn.execute(
                "SELECT status FROM payg_provision_operations WHERE id=?",
                (int(operation_id),),
            ) as cur:
                current = await cur.fetchone()
            if current is None or current["status"] == "completed":
                await conn.rollback()
                raise PaygOfferError("operation already finalized")
            async with conn.execute(
                "SELECT a.id,a.admin_id FROM payg_accounts a JOIN payg_account_services s "
                "ON s.account_id=a.id WHERE a.user_id=? AND s.service_id=? "
                "AND a.status!='migrated' ORDER BY a.id DESC LIMIT 1",
                (user_id,service_id),
            ) as cur:
                existing = await cur.fetchone()
            if existing:
                if int(existing["admin_id"]) != int(local.id):
                    await conn.rollback()
                    raise PaygOfferError("existing PAYG account is linked to another remote admin")
                account_id = int(existing["id"])
            else:
                cur = await conn.execute(
                    "INSERT INTO payg_accounts "
                    "(user_id,admin_id,provider,status,price_per_gb_toman,last_raw_usage_bytes) "
                    "VALUES(?,?,'rebecca','active',?,?)",
                    (user_id,int(local.id),int(offer["price_per_gb_toman"]),baseline),
                )
                account_id = int(cur.lastrowid)
                await conn.execute(
                    "INSERT INTO payg_account_services "
                    "(account_id,provider,service_id,display_name_snapshot) "
                    "VALUES(?,'rebecca',?,?)",
                    (account_id,service_id,str(service.display_name)),
                )
            await conn.execute(
                "UPDATE payg_provision_operations SET status='completed',"
                "last_error=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (int(operation_id),),
            )
            await conn.execute(
                "INSERT INTO payg_provision_audit(operation_id,event_type,detail) "
                "VALUES(?,'owner_adoption',?)",
                (int(operation_id),f"owner={actor_id}; account={account_id}; "
                 f"baseline={baseline}; {reason.strip()[:250]}"),
            )
            await conn.commit()
        account = await payg_service.get_account(account_id)
        if account is None:
            raise PaygOfferError("adopted account cannot be loaded")
        return account

    async def provision(self, user_id: int, service_id: int) -> dict[str, Any]:
        """Durably reserve exactly one remote identity before any network call.

        An ambiguous network outcome is never retried as a CREATE: the same
        identity must first be reconciled against Rebecca. Never delete a
        remotely created admin as compensation for a local database failure.
        """
        existing = await self.account_for_user_service(user_id, service_id)
        if existing:
            return existing
        offer = await self.get_offer(service_id)
        if not offer or not int(offer["enabled"]):
            raise PaygOfferError("PAYG service is disabled")
        service = await get_service_by_rebecca_id(int(service_id))
        if not service or not service.is_enabled:
            raise PaygOfferError("Rebecca service is disabled")
        rate = int(offer["price_per_gb_toman"] or 0)
        if rate <= 0:
            raise PaygOfferError("PAYG rate is not configured")
        balance = await payg_service.get_balance(int(user_id))
        minimum = int(offer["min_topup_toman"] or 0)
        if balance < minimum:
            raise PaygInsufficientFunds(minimum - balance)
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            await conn.execute("BEGIN IMMEDIATE")
            async with conn.execute(
                "SELECT * FROM payg_provision_operations "
                "WHERE user_id=? AND provider='rebecca' AND service_id=?",
                (int(user_id), int(service_id)),
            ) as cur:
                operation = await cur.fetchone()
            if operation is None:
                # Stable across retries and process restarts, and bounded to
                # Rebecca's username constraints.
                username = f"payg{int(user_id)}_{int(service_id)}"
                if len(username) > 24:
                    await conn.rollback()
                    raise PaygOfferError("PAYG identity exceeds Rebecca username limit")
                password = secrets.token_urlsafe(10)
                await conn.execute(
                    "INSERT INTO payg_provision_operations "
                    "(user_id,service_id,remote_username,remote_password,status) "
                    "VALUES(?,?,?,?,'reserved')",
                    (int(user_id),int(service_id),username,password),
                )
                status = "reserved"
            else:
                username = str(operation["remote_username"])
                password = str(operation["remote_password"])
                status = str(operation["status"])
            if status != "reserved":
                await conn.commit()
                raise PaygOfferError(
                    "PAYG provisioning pending reconciliation; no duplicate CREATE attempted"
                )
            await conn.execute(
                "UPDATE payg_provision_operations SET status='remote_pending',"
                "updated_at=CURRENT_TIMESTAMP WHERE user_id=? AND service_id=?",
                (int(user_id),int(service_id)),
            )
            await conn.execute(
                "INSERT INTO payg_provision_audit(operation_id,event_type,detail) "
                "SELECT id,'remote_create_started',? FROM payg_provision_operations "
                "WHERE user_id=? AND provider='rebecca' AND service_id=?",
                ("create request may be ambiguous after process crash",
                 int(user_id),int(service_id)),
            )
            await conn.commit()
        try:
            # Existing remote username must never be overwritten or adopted
            # without a separate operator ownership verification.
            remote = await rebecca_api.find_admin(username)
            if remote is not None:
                raise PaygOfferError("remote identity exists; manual ownership reconciliation required")
            await rebecca_api.create_admin_verified(
                username,password,int(user_id),data_limit=None,expire=None,
                users_limit=int(offer["max_users"]),services=[int(service_id)],
            )
            baseline = int(await rebecca_api.get_admin_usage(username))
            if baseline < 0:
                raise PaygOfferError("invalid remote counter")
            local = await db.get_admin_by_marzban_username(username)
            if local is None:
                model = AdminModel(
                    user_id=int(user_id),admin_name=f"PAYG {service.display_name}",
                    marzban_username=username,marzban_password=password,
                    max_users=int(offer["max_users"]),
                    max_total_time=_LONG_VALIDITY_DAYS * 86400,
                    max_total_traffic=0,validity_days=_LONG_VALIDITY_DAYS,
                    is_active=True,origin_plan_id=None,
                    allow_incremental_renewal=False,
                )
                if not await db.add_admin(model):
                    raise PaygOfferError("local admin persistence failed")
                local = await db.get_admin_by_marzban_username(username)
            if not local or local.id is None or int(local.user_id) != int(user_id):
                raise PaygOfferError("local admin ownership mismatch")
            async with aiosqlite.connect(self.db_path) as conn:
                await conn.execute("BEGIN IMMEDIATE")
                cur = await conn.execute(
                    "INSERT INTO payg_accounts "
                    "(user_id,admin_id,provider,status,price_per_gb_toman,last_raw_usage_bytes) "
                    "VALUES(?,?,?,?,?,?)",
                    (int(user_id),int(local.id),"rebecca","active",rate,baseline),
                )
                account_id = int(cur.lastrowid)
                await conn.execute(
                    "INSERT INTO payg_account_services "
                    "(account_id,provider,service_id,display_name_snapshot) "
                    "VALUES(?,'rebecca',?,?)",
                    (account_id,int(service_id),str(service.display_name)),
                )
                await conn.execute(
                    "UPDATE payg_provision_operations SET status='completed',"
                    "last_error=NULL,updated_at=CURRENT_TIMESTAMP "
                    "WHERE user_id=? AND service_id=?",
                    (int(user_id),int(service_id)),
                )
                await conn.execute(
                    "INSERT INTO payg_provision_audit(operation_id,event_type,detail) "
                    "SELECT id,'created_and_linked',? FROM payg_provision_operations "
                    "WHERE user_id=? AND provider='rebecca' AND service_id=?",
                    (f"account={account_id}; baseline={baseline}",
                     int(user_id),int(service_id)),
                )
                await conn.commit()
            return await payg_service.get_account(account_id)
        except Exception as exc:
            async with aiosqlite.connect(self.db_path) as conn:
                await conn.execute(
                    "UPDATE payg_provision_operations SET status='reconcile',"
                    "last_error=?,updated_at=CURRENT_TIMESTAMP "
                    "WHERE user_id=? AND service_id=? AND status!='completed'",
                    (str(exc)[:400],int(user_id),int(service_id)),
                )
                await conn.execute(
                    "INSERT INTO payg_provision_audit(operation_id,event_type,detail) "
                    "SELECT id,'ambiguous_or_failed',? FROM payg_provision_operations "
                    "WHERE user_id=? AND provider='rebecca' AND service_id=? "
                    "AND status='reconcile'",
                    (str(exc)[:400],int(user_id),int(service_id)),
                )
                await conn.commit()
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
        if account_id is not None:
            account = await self.account_for_user_service(int(user_id), int(service_id))
            if not account or int(account["id"]) != int(account_id):
                raise PaygOfferError("top-up service account ownership mismatch")
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
