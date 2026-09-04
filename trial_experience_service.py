from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math
import re
import secrets
import time
from typing import Any

import aiosqlite

import config
from database import db
from models.schemas import AdminModel
from operations_service import OperationsError, operations_service
from utils.notify import days_to_seconds
from utils.rebecca import parse_service_ids, credential_message


_USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{2,31}$")
_TRIAL_TYPES = {"panel", "config"}


@dataclass(frozen=True)
class OrderIssueResult:
    order_id: int
    user_id: int
    username: str
    password: str
    login_url: str
    plan_name: str


class TrialExperienceService:
    def __init__(self, db_path: str | None = None):
        self._db_path = db_path

    @property
    def db_path(self) -> str:
        return self._db_path or config.DATABASE_PATH

    async def ensure_schema(self) -> None:
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS order_preferences (
                    order_id INTEGER PRIMARY KEY,
                    requested_username TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE INDEX IF NOT EXISTS idx_order_preferences_username
                    ON order_preferences(requested_username);

                CREATE TABLE IF NOT EXISTS order_issue_locks (
                    order_id INTEGER PRIMARY KEY,
                    requested_username TEXT NOT NULL,
                    password TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'creating',
                    lease_at INTEGER NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS trial_plan_access (
                    plan_id INTEGER NOT NULL,
                    trial_type TEXT NOT NULL CHECK(trial_type IN ('panel','config')),
                    is_enabled INTEGER NOT NULL DEFAULT 1,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(plan_id, trial_type)
                );

                CREATE TABLE IF NOT EXISTS panel_trial_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS panel_trial_issues (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    provider TEXT NOT NULL,
                    provider_username TEXT NOT NULL,
                    plan_id INTEGER NOT NULL,
                    expire_at INTEGER NOT NULL,
                    created_at INTEGER NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_panel_trial_user_created
                    ON panel_trial_issues(user_id, created_at DESC);
                """
            )
            defaults = {
                "enabled": "0",
                "traffic_bytes": str(10 * 1024**3),
                "duration_seconds": str(24 * 60 * 60),
                "max_users": "5",
                "cooldown_seconds": str(7 * 24 * 60 * 60),
            }
            for key, value in defaults.items():
                await conn.execute(
                    "INSERT OR IGNORE INTO panel_trial_settings(key,value) VALUES(?,?)",
                    (key, value),
                )
            await conn.commit()

    @staticmethod
    def validate_username(value: str) -> str:
        username = str(value or "").strip().lower()
        if not _USERNAME_RE.fullmatch(username):
            raise OperationsError(
                "نام کاربری باید ۳ تا ۳۲ کاراکتر و فقط شامل حروف انگلیسی کوچک، عدد، نقطه، _ یا - باشد."
            )
        return username

    async def save_order_username(self, order_id: int, username: str) -> None:
        await self.ensure_schema()
        username = self.validate_username(username)
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                """
                INSERT INTO order_preferences(order_id,requested_username,updated_at)
                VALUES(?,?,CURRENT_TIMESTAMP)
                ON CONFLICT(order_id) DO UPDATE SET
                    requested_username=excluded.requested_username,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (int(order_id), username),
            )
            await conn.commit()

    async def get_order_username(self, order_id: int) -> str | None:
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            async with conn.execute(
                "SELECT requested_username FROM order_preferences WHERE order_id=?",
                (int(order_id),),
            ) as cur:
                row = await cur.fetchone()
        return str(row[0]) if row else None

    async def username_available(self, username: str) -> bool:
        username = self.validate_username(username)
        local = await db.get_admin_by_marzban_username(username)
        if local is not None:
            return False
        provider = str(config.PANEL_PROVIDER or "marzban").lower()
        if provider == "rebecca":
            from rebecca_api import rebecca_api

            return await rebecca_api.find_admin(username) is None
        if provider == "marzban":
            from marzban_api import marzban_api

            return not bool(await marzban_api.admin_exists(username))
        raise OperationsError("Provider فعلی برای ساخت پنل پشتیبانی نمی‌شود.")

    async def set_plan_enabled(self, plan_id: int, trial_type: str, enabled: bool) -> None:
        if trial_type not in _TRIAL_TYPES:
            raise OperationsError("نوع تست نامعتبر است.")
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                """
                INSERT INTO trial_plan_access(plan_id,trial_type,is_enabled,updated_at)
                VALUES(?,?,?,CURRENT_TIMESTAMP)
                ON CONFLICT(plan_id,trial_type) DO UPDATE SET
                    is_enabled=excluded.is_enabled,updated_at=CURRENT_TIMESTAMP
                """,
                (int(plan_id), trial_type, 1 if enabled else 0),
            )
            await conn.commit()

    async def plan_enabled(self, plan_id: int, trial_type: str) -> bool:
        if trial_type not in _TRIAL_TYPES:
            return False
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            async with conn.execute(
                "SELECT is_enabled FROM trial_plan_access WHERE plan_id=? AND trial_type=?",
                (int(plan_id), trial_type),
            ) as cur:
                row = await cur.fetchone()
        # Backward-friendly default: existing active plans are trial-visible until explicitly disabled.
        return True if row is None else bool(row[0])

    async def list_trial_plans(self, trial_type: str) -> list[Any]:
        if trial_type not in _TRIAL_TYPES:
            return []
        plans = await db.get_plans(only_active=True)
        result = []
        for plan in plans:
            if await self.plan_enabled(int(plan.id), trial_type):
                result.append(plan)
        return result

    async def get_panel_trial_settings(self) -> dict[str, Any]:
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            async with conn.execute("SELECT key,value FROM panel_trial_settings") as cur:
                values = {str(k): str(v or "") for k, v in await cur.fetchall()}

        def as_int(key: str, default: int) -> int:
            try:
                return int(values.get(key, default))
            except (TypeError, ValueError):
                return default

        return {
            "enabled": values.get("enabled", "0") == "1",
            "traffic_bytes": max(1, as_int("traffic_bytes", 10 * 1024**3)),
            "duration_seconds": max(300, as_int("duration_seconds", 24 * 60 * 60)),
            "max_users": max(1, as_int("max_users", 5)),
            "cooldown_seconds": max(0, as_int("cooldown_seconds", 7 * 24 * 60 * 60)),
        }

    async def set_panel_trial_setting(self, key: str, value: str | int | bool) -> None:
        allowed = {"enabled", "traffic_bytes", "duration_seconds", "max_users", "cooldown_seconds"}
        if key not in allowed:
            raise OperationsError("تنظیم تست پنل ناشناخته است.")
        if isinstance(value, bool):
            raw = "1" if value else "0"
        else:
            raw = str(value)
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                """
                INSERT INTO panel_trial_settings(key,value,updated_at)
                VALUES(?,?,CURRENT_TIMESTAMP)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=CURRENT_TIMESTAMP
                """,
                (key, raw),
            )
            await conn.commit()

    async def panel_trial_wait_seconds(self, user_id: int) -> int:
        settings = await self.get_panel_trial_settings()
        cooldown = int(settings["cooldown_seconds"])
        if cooldown <= 0:
            return 0
        async with aiosqlite.connect(self.db_path) as conn:
            async with conn.execute(
                "SELECT created_at FROM panel_trial_issues WHERE user_id=? ORDER BY id DESC LIMIT 1",
                (int(user_id),),
            ) as cur:
                row = await cur.fetchone()
        if not row:
            return 0
        return max(0, int(row[0]) + cooldown - int(time.time()))

    async def _record_panel_trial(
        self, *, user_id: int, provider: str, username: str, plan_id: int, expire_at: int
    ) -> None:
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                """
                INSERT INTO panel_trial_issues(
                    user_id,provider,provider_username,plan_id,expire_at,created_at
                ) VALUES(?,?,?,?,?,?)
                """,
                (int(user_id), provider, username, int(plan_id), int(expire_at), int(time.time())),
            )
            await conn.commit()

    @staticmethod
    def _services_for_plan(plan: Any) -> list[int]:
        mapping = getattr(plan, "rebecca_service_ids", None)
        if mapping is not None:
            try:
                services = parse_service_ids(mapping)
            except ValueError as exc:
                raise OperationsError("سرویس‌های Rebecca این پلن نامعتبر هستند.") from exc
        else:
            services = [int(x) for x in getattr(config, "REBECCA_SERVICE_IDS", []) if int(x) > 0]
        if not services:
            raise OperationsError("برای این پلن سرویس Rebecca تعریف نشده است.")
        return services

    async def config_service_options(self, plan_id: int) -> list[dict[str, Any]]:
        plan = await db.get_plan_by_id(int(plan_id))
        if not plan or not getattr(plan, "is_active", True):
            return []
        if str(config.PANEL_PROVIDER or "").lower() != "rebecca":
            return [{"service_id": 0, "name": "مرزبان اصلی"}]
        service_ids = self._services_for_plan(plan)
        names: dict[int, str] = {}
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            try:
                placeholders = ",".join("?" for _ in service_ids)
                async with conn.execute(
                    f"SELECT service_id,display_name,service_name FROM rebecca_services WHERE is_active=1 AND service_id IN ({placeholders})",
                    tuple(service_ids),
                ) as cur:
                    for row in await cur.fetchall():
                        sid = int(row["service_id"])
                        names[sid] = str(row["display_name"] or row["service_name"] or f"Service {sid}")
            except aiosqlite.OperationalError:
                pass
        return [{"service_id": sid, "name": names.get(sid, f"Service {sid}")} for sid in service_ids]

    @staticmethod
    def _trial_username(user_id: int) -> str:
        return f"test_{int(user_id)}_{secrets.token_hex(3)}"[:32]

    async def issue_config_trial(self, user_id: int, plan_id: int, service_id: int | None = None) -> dict[str, Any]:
        if not await self.plan_enabled(plan_id, "config"):
            raise OperationsError("این پنل برای تست رایگان کانفیگ فعال نیست.")
        plan = await db.get_plan_by_id(int(plan_id))
        if not plan or not getattr(plan, "is_active", True):
            raise OperationsError("پنل انتخاب‌شده در دسترس نیست.")
        settings = await operations_service.get_trial_settings()
        if not settings["enabled"]:
            raise OperationsError("تست رایگان کانفیگ فعلاً غیرفعال است.")
        wait = await operations_service.trial_wait_seconds(user_id)
        if wait > 0:
            hours = max(1, math.ceil(wait / 3600))
            raise OperationsError(f"برای دریافت تست بعدی حدود {hours} ساعت صبر کنید.")

        provider = str(config.PANEL_PROVIDER or "marzban").lower()
        username = self._trial_username(user_id)
        expire_at = int(time.time()) + int(settings["duration_seconds"])
        data_limit = int(settings["traffic_bytes"])
        subscription_url: str | None = None
        links: list[str] = []
        selected_service: int | None = None

        if provider == "rebecca":
            from rebecca_api import rebecca_api, RebeccaAPIError

            allowed = self._services_for_plan(plan)
            if service_id is None:
                if len(allowed) != 1:
                    raise OperationsError("سرویس کانفیگ تست را انتخاب کنید.")
                selected_service = allowed[0]
            else:
                selected_service = int(service_id)
                if selected_service not in allowed:
                    raise OperationsError("سرویس انتخاب‌شده متعلق به این پنل نیست.")
            payload = {
                "username": username,
                "status": "active",
                "expire": expire_at,
                "data_limit": data_limit,
                "ip_limit": None,
                "data_limit_reset_strategy": "no_reset",
                "on_hold_expire_duration": None,
                "note": f"Wingmarz free config trial / plan {plan.id}",
                "telegram_id": str(int(user_id)),
                "contact_number": "",
                "flow": "",
                "service_id": selected_service,
                "auto_delete_in_days": 1,
            }
            try:
                result = await rebecca_api._request("POST", "/api/user", json=payload)
            except RebeccaAPIError as exc:
                raise OperationsError("ساخت کانفیگ تست در Rebecca ناموفق بود.") from exc
            if not isinstance(result, dict) or str(result.get("username", "")) != username:
                raise OperationsError("پاسخ Rebecca برای کانفیگ تست معتبر نبود.")
            subscription_url = str(result.get("subscription_url") or result.get("key_subscription_url") or "").strip() or None
            if isinstance(result.get("subscription_urls"), dict):
                links.extend(str(v) for v in result["subscription_urls"].values() if v)
            if isinstance(result.get("links"), list):
                links.extend(str(v) for v in result["links"] if v)
        elif provider == "marzban":
            from marzban_api import marzban_api

            try:
                inbound_response = await marzban_api._request("GET", f"{marzban_api.base_url}/api/inbounds")
            except Exception as exc:
                raise OperationsError("دریافت inboundهای مرزبان ناموفق بود.") from exc
            if inbound_response.status_code != 200:
                raise OperationsError("مرزبان لیست inbound معتبر برنگرداند.")
            try:
                inbound_data = inbound_response.json()
            except ValueError as exc:
                raise OperationsError("پاسخ inbound مرزبان معتبر نبود.") from exc
            selected_protocol = selected_tag = None
            if isinstance(inbound_data, dict):
                for protocol in ("vless", "vmess", "trojan", "shadowsocks"):
                    items = inbound_data.get(protocol)
                    if isinstance(items, list) and items:
                        first = items[0]
                        tag = first.get("tag") if isinstance(first, dict) else None
                        if tag:
                            selected_protocol, selected_tag = protocol, str(tag)
                            break
            if not selected_protocol or not selected_tag:
                raise OperationsError("هیچ inbound قابل استفاده‌ای برای تست پیدا نشد.")
            payload = {
                "username": username,
                "proxies": {selected_protocol: {}},
                "inbounds": {selected_protocol: [selected_tag]},
                "expire": expire_at,
                "data_limit": data_limit,
                "data_limit_reset_strategy": "no_reset",
                "on_hold_expire_duration": None,
                "status": "active",
                "note": f"Wingmarz free config trial / plan {plan.id}",
            }
            try:
                response = await marzban_api._request("POST", f"{marzban_api.base_url}/api/user", json=payload)
            except Exception as exc:
                raise OperationsError("ساخت کانفیگ تست در مرزبان ناموفق بود.") from exc
            if response.status_code not in (200, 201):
                raise OperationsError("مرزبان کانفیگ تست را ایجاد نکرد.")
            try:
                result = response.json()
            except ValueError as exc:
                raise OperationsError("پاسخ ساخت کاربر مرزبان معتبر نبود.") from exc
            if not isinstance(result, dict) or str(result.get("username", "")) != username:
                raise OperationsError("پاسخ مرزبان برای کانفیگ تست معتبر نبود.")
            subscription_url = str(result.get("subscription_url") or "").strip() or None
            if isinstance(result.get("links"), list):
                links.extend(str(v) for v in result["links"] if v)
        else:
            raise OperationsError("Provider فعلی برای کانفیگ تست پشتیبانی نمی‌شود.")

        await operations_service.record_trial(
            user_id=user_id,
            provider=provider,
            provider_username=username,
            service_id=selected_service,
            subscription_url=subscription_url,
            expire_at=expire_at,
        )
        unique_links: list[str] = []
        for item in [subscription_url, *links]:
            if item and item not in unique_links:
                unique_links.append(item)
        return {
            "provider": provider,
            "plan_id": int(plan.id),
            "plan_name": str(plan.name),
            "username": username,
            "subscription_url": subscription_url,
            "links": unique_links,
            "expire_at": expire_at,
            "traffic_bytes": data_limit,
            "service_id": selected_service,
        }

    async def issue_panel_trial(
        self,
        *,
        user_id: int,
        plan_id: int,
        requested_username: str,
        telegram_username: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
    ) -> dict[str, Any]:
        settings = await self.get_panel_trial_settings()
        if not settings["enabled"]:
            raise OperationsError("تست پنل فعلاً غیرفعال است.")
        if not await self.plan_enabled(plan_id, "panel"):
            raise OperationsError("این پنل برای تست رایگان فعال نیست.")
        wait = await self.panel_trial_wait_seconds(user_id)
        if wait > 0:
            hours = max(1, math.ceil(wait / 3600))
            raise OperationsError(f"برای دریافت تست پنل بعدی حدود {hours} ساعت صبر کنید.")
        plan = await db.get_plan_by_id(int(plan_id))
        if not plan or not getattr(plan, "is_active", True):
            raise OperationsError("پنل انتخاب‌شده در دسترس نیست.")
        username = self.validate_username(requested_username)
        if not await self.username_available(username):
            raise OperationsError("این نام کاربری قبلاً استفاده شده است؛ یک نام دیگر انتخاب کنید.")

        provider = str(config.PANEL_PROVIDER or "marzban").lower()
        password = secrets.token_urlsafe(18) if provider == "rebecca" else secrets.token_hex(5)
        expire_at = int(time.time()) + int(settings["duration_seconds"])
        created = False
        if provider == "rebecca":
            from rebecca_api import rebecca_api, RebeccaConflict

            services = self._services_for_plan(plan)
            try:
                await rebecca_api.create_admin_verified(
                    username,
                    password,
                    int(user_id),
                    data_limit=int(settings["traffic_bytes"]),
                    expire=expire_at,
                    users_limit=int(settings["max_users"]),
                    services=services,
                )
                created = True
            except RebeccaConflict as exc:
                raise OperationsError("این نام کاربری هم‌زمان در Rebecca استفاده شد؛ نام دیگری انتخاب کنید.") from exc
        elif provider == "marzban":
            from marzban_api import marzban_api

            created = bool(await marzban_api.create_admin(username, password, telegram_id=int(user_id), is_sudo=False))
            if not created:
                raise OperationsError("ساخت پنل تست در مرزبان ناموفق بود.")
        else:
            raise OperationsError("Provider فعلی برای تست پنل پشتیبانی نمی‌شود.")

        full_name = " ".join(part for part in (first_name, last_name) if part).strip()
        identity = full_name or (f"@{telegram_username}" if telegram_username else f"User {user_id}")
        admin = AdminModel(
            user_id=int(user_id),
            admin_name=f"تست {plan.name}",
            marzban_username=username,
            marzban_password=password,
            username=telegram_username,
            first_name=first_name,
            last_name=last_name,
            max_users=int(settings["max_users"]),
            max_total_time=int(settings["duration_seconds"]),
            max_total_traffic=int(settings["traffic_bytes"]),
            validity_days=max(1, math.ceil(int(settings["duration_seconds"]) / 86400)),
            is_active=True,
            origin_plan_id=int(plan.id),
            allow_incremental_renewal=False,
        )
        saved = await db.add_admin(admin)
        if not saved:
            local = await db.get_admin_by_marzban_username(username)
            if not local or int(local.user_id) != int(user_id):
                raise OperationsError("پنل ساخته شد اما ثبت محلی آن ناموفق بود؛ با پشتیبانی تماس بگیرید.")
        await self._record_panel_trial(
            user_id=int(user_id), provider=provider, username=username, plan_id=int(plan.id), expire_at=expire_at
        )
        login_url = await db.get_setting("global_login_url")
        if not login_url:
            login_url = (
                (getattr(config, "REBECCA_LOGIN_URL", "") or getattr(config, "REBECCA_URL", ""))
                if provider == "rebecca"
                else getattr(config, "MARZBAN_URL", "")
            )
        return {
            "provider": provider,
            "username": username,
            "password": password,
            "plan_id": int(plan.id),
            "plan_name": str(plan.name),
            "login_url": str(login_url or ""),
            "expire_at": expire_at,
            "traffic_bytes": int(settings["traffic_bytes"]),
            "max_users": int(settings["max_users"]),
            "identity": identity,
        }

    async def _acquire_order_lock(self, order_id: int, username: str) -> tuple[str, bool]:
        await self.ensure_schema()
        now = int(time.time())
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            await conn.execute("BEGIN IMMEDIATE")
            async with conn.execute("SELECT * FROM order_issue_locks WHERE order_id=?", (int(order_id),)) as cur:
                row = await cur.fetchone()
            if row:
                if str(row["state"]) == "completed":
                    await conn.rollback()
                    raise OperationsError("این سفارش قبلاً صادر شده است.")
                lease_at = int(row["lease_at"] or 0)
                if lease_at > now - 300:
                    await conn.rollback()
                    raise OperationsError("صدور این سفارش در حال انجام است.")
                await conn.execute(
                    "UPDATE order_issue_locks SET lease_at=?,updated_at=CURRENT_TIMESTAMP WHERE order_id=?",
                    (now, int(order_id)),
                )
                await conn.commit()
                return str(row["password"]), True
            password = secrets.token_urlsafe(18) if str(config.PANEL_PROVIDER).lower() == "rebecca" else secrets.token_hex(5)
            await conn.execute(
                "INSERT INTO order_issue_locks(order_id,requested_username,password,state,lease_at) VALUES(?,?,?,?,?)",
                (int(order_id), username, password, "creating", now),
            )
            await conn.commit()
            return password, False

    async def _release_order_lock(self, order_id: int) -> None:
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute("DELETE FROM order_issue_locks WHERE order_id=? AND state='creating'", (int(order_id),))
            await conn.commit()

    async def _complete_order_lock(self, order_id: int) -> None:
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                "UPDATE order_issue_locks SET state='completed',updated_at=CURRENT_TIMESTAMP WHERE order_id=?",
                (int(order_id),),
            )
            await conn.commit()

    async def approve_preferred_order(self, order_id: int, approved_by: int, bot: Any) -> OrderIssueResult:
        order = await db.get_order_by_id(int(order_id))
        if not order:
            raise OperationsError("سفارش یافت نشد.")
        if str(order.get("status") or "").lower() == "approved":
            raise OperationsError("این سفارش قبلاً تایید شده است.")
        order_type = str(order.get("order_type") or "").lower()
        if order_type.startswith("renew"):
            raise OperationsError("این سفارش تمدید است و باید با مسیر قبلی پردازش شود.")
        username = await self.get_order_username(int(order_id))
        if not username:
            raise OperationsError("برای این سفارش نام کاربری دلخواه ثبت نشده است.")
        username = self.validate_username(username)
        plan = await db.get_plan_by_id(int(order.get("plan_id") or 0))
        if not plan:
            raise OperationsError("پلن سفارش یافت نشد.")
        password, recovery = await self._acquire_order_lock(int(order_id), username)
        provider = str(config.PANEL_PROVIDER or "marzban").lower()

        local = await db.get_admin_by_marzban_username(username)
        if local:
            if int(local.user_id) != int(order["user_id"]):
                await self._release_order_lock(order_id)
                raise OperationsError("نام کاربری با پنل دیگری تداخل دارد.")
            password = str(local.marzban_password or password)
            await db.update_order(
                int(order_id), status="approved", approved_by=int(approved_by), issued_admin_id=int(local.id)
            )
            await self._complete_order_lock(order_id)
            login_url = await db.get_setting("global_login_url") or local.login_url or (
                (getattr(config, "REBECCA_LOGIN_URL", "") or getattr(config, "REBECCA_URL", ""))
                if provider == "rebecca" else getattr(config, "MARZBAN_URL", "")
            )
            return OrderIssueResult(int(order_id), int(order["user_id"]), username, password, str(login_url or ""), str(plan.name))

        try:
            if provider == "rebecca":
                from rebecca_api import rebecca_api, RebeccaConflict, RebeccaAPIError

                services = self._services_for_plan(plan)
                expire = int(time.time()) + int(plan.time_limit_seconds) if plan.time_limit_seconds is not None else None
                existing = await rebecca_api.find_admin(username)
                if existing is not None:
                    if not recovery:
                        await self._release_order_lock(order_id)
                        raise OperationsError("این نام کاربری دیگر در دسترس نیست؛ کاربر باید نام دیگری انتخاب کند.")
                    rebecca_api.verify_admin(
                        existing,
                        username,
                        int(order["user_id"]),
                        data_limit=plan.traffic_limit_bytes,
                        expire=expire,
                        users_limit=plan.max_users,
                        services=services,
                    )
                else:
                    try:
                        await rebecca_api.create_admin_verified(
                            username,
                            password,
                            int(order["user_id"]),
                            data_limit=plan.traffic_limit_bytes,
                            expire=expire,
                            users_limit=plan.max_users,
                            services=services,
                        )
                    except RebeccaConflict as exc:
                        await self._release_order_lock(order_id)
                        raise OperationsError("این نام کاربری دیگر در دسترس نیست؛ کاربر باید نام دیگری انتخاب کند.") from exc
            elif provider == "marzban":
                from marzban_api import marzban_api

                exists = bool(await marzban_api.admin_exists(username))
                if exists:
                    if not recovery:
                        await self._release_order_lock(order_id)
                        raise OperationsError("این نام کاربری دیگر در دسترس نیست؛ کاربر باید نام دیگری انتخاب کند.")
                    admin_api = await marzban_api.create_admin_api(username, password)
                    if not await admin_api.test_connection():
                        await self._release_order_lock(order_id)
                        raise OperationsError("نام کاربری با یک پنل موجود تداخل دارد.")
                else:
                    created = await marzban_api.create_admin(username, password, telegram_id=int(order["user_id"]), is_sudo=False)
                    if not created:
                        raise OperationsError("صدور پنل در مرزبان ناموفق بود.")
            else:
                await self._release_order_lock(order_id)
                raise OperationsError("Provider فعلی برای صدور پنل پشتیبانی نمی‌شود.")
        except OperationsError:
            raise
        except Exception as exc:
            # Keep the lease for a short recovery window when remote outcome may be ambiguous.
            raise OperationsError("وضعیت صدور پنل نامشخص است؛ چند دقیقه بعد دوباره تلاش کنید.") from exc

        telegram_username = first_name = last_name = None
        try:
            customer = await bot.get_chat(int(order["user_id"]))
            telegram_username = getattr(customer, "username", None)
            first_name = getattr(customer, "first_name", None)
            last_name = getattr(customer, "last_name", None)
        except Exception:
            pass
        full_name = " ".join(part for part in (first_name, last_name) if part).strip()
        identity = full_name or (f"@{telegram_username}" if telegram_username else f"User {order['user_id']}")
        admin = AdminModel(
            user_id=int(order["user_id"]),
            admin_name=(identity if provider == "rebecca" else (str(plan.name) or f"Reseller #{order_id}")),
            marzban_username=username,
            marzban_password=password,
            username=telegram_username if provider == "rebecca" else None,
            first_name=first_name if provider == "rebecca" else None,
            last_name=last_name if provider == "rebecca" else None,
            max_users=(plan.max_users if plan.max_users is not None else 1000000),
            max_total_time=(plan.time_limit_seconds if plan.time_limit_seconds is not None else days_to_seconds(36500)),
            max_total_traffic=plan.traffic_limit_bytes or 0,
            validity_days=(plan.time_limit_seconds // 86400) if plan.time_limit_seconds else 36500,
            is_active=True,
            origin_plan_id=int(plan.id),
            allow_incremental_renewal=(plan.allow_incremental_renewal if provider == "rebecca" else None),
        )
        saved = await db.add_admin(admin)
        if not saved:
            existing_local = await db.get_admin_by_marzban_username(username)
            if not existing_local or int(existing_local.user_id) != int(order["user_id"]):
                raise OperationsError("پنل ساخته شد اما ثبت محلی ناموفق بود؛ صدور مجدد متوقف شد.")
            issued = existing_local
        else:
            issued = await db.get_admin_by_marzban_username(username)
        await db.update_order(
            int(order_id),
            status="approved",
            approved_by=int(approved_by),
            issued_admin_id=(int(issued.id) if issued else None),
        )
        await self._complete_order_lock(order_id)
        login_url = await db.get_setting("global_login_url")
        if not login_url:
            login_url = (
                (getattr(config, "REBECCA_LOGIN_URL", "") or getattr(config, "REBECCA_URL", ""))
                if provider == "rebecca"
                else getattr(config, "MARZBAN_URL", "")
            )
        return OrderIssueResult(
            order_id=int(order_id),
            user_id=int(order["user_id"]),
            username=username,
            password=password,
            login_url=str(login_url or ""),
            plan_name=str(plan.name),
        )

    @staticmethod
    def credential_text(result: OrderIssueResult) -> str:
        return credential_message(result.username, result.password, result.login_url, result.plan_name)


trial_experience_service = TrialExperienceService()
