from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import math
import secrets
import time
from typing import Any

import aiosqlite

import config
from database import db
from models.schemas import AdminModel
from operations_service import OperationsError, operations_service
from rebecca_api import RebeccaAPIError, RebeccaConflict, rebecca_api
from rebecca_catalog import RebeccaServiceRecord, get_service_by_rebecca_id, list_services
from trial_experience_service import trial_experience_service
from utils.rebecca import parse_service_ids


@dataclass(frozen=True)
class DurationGroup:
    key: str
    seconds: int | None
    label: str
    plans: tuple[Any, ...]


class ServiceMarketplaceService:
    """User-facing Rebecca marketplace organised around Rebecca services.

    Rebecca catalog services are the product types shown to customers
    (WireGuard/OpenVPN/etc). Plans remain the commercial offers attached to one
    or more service IDs. This keeps provider identity separate from pricing.
    """

    def __init__(self, db_path: str | None = None):
        self._db_path = db_path

    @property
    def db_path(self) -> str:
        return self._db_path or config.DATABASE_PATH

    async def ensure_schema(self) -> None:
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS service_market_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS service_trial_access (
                    rebecca_service_id INTEGER PRIMARY KEY,
                    panel_enabled INTEGER NOT NULL DEFAULT 1,
                    config_enabled INTEGER NOT NULL DEFAULT 1,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
            await conn.execute(
                "INSERT OR IGNORE INTO service_market_settings(key,value) VALUES('duration_groups_enabled','1')"
            )
            await conn.commit()

    async def duration_groups_enabled(self) -> bool:
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            async with conn.execute(
                "SELECT value FROM service_market_settings WHERE key='duration_groups_enabled'"
            ) as cur:
                row = await cur.fetchone()
        return True if row is None else str(row[0]).lower() in {"1", "true", "yes", "on"}

    async def set_duration_groups_enabled(self, enabled: bool) -> None:
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                """
                INSERT INTO service_market_settings(key,value,updated_at)
                VALUES('duration_groups_enabled',?,CURRENT_TIMESTAMP)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=CURRENT_TIMESTAMP
                """,
                ("1" if enabled else "0",),
            )
            await conn.commit()

    @staticmethod
    def plan_service_ids(plan: Any) -> list[int]:
        raw = getattr(plan, "rebecca_service_ids", None)
        if raw is None:
            return []
        try:
            return [int(item) for item in parse_service_ids(raw) if int(item) > 0]
        except (TypeError, ValueError):
            return []

    async def plans_for_service(self, service_id: int) -> list[Any]:
        service_id = int(service_id)
        plans = await db.get_plans(only_active=True)
        return [plan for plan in plans if service_id in self.plan_service_ids(plan)]

    async def sellable_services(self) -> list[tuple[RebeccaServiceRecord, int]]:
        result: list[tuple[RebeccaServiceRecord, int]] = []
        for service in await list_services(enabled_only=True):
            plans = await self.plans_for_service(service.rebecca_service_id)
            if plans:
                result.append((service, len(plans)))
        return result

    @staticmethod
    def duration_label(seconds: int | None) -> str:
        if seconds is None or int(seconds) <= 0:
            return "بدون محدودیت زمانی"
        seconds = int(seconds)
        days = max(1, math.ceil(seconds / 86400))
        if days in {360, 365}:
            return "1 ساله"
        if days % 30 == 0:
            months = days // 30
            return f"{months} ماهه"
        return f"{days} روزه"

    @classmethod
    def group_plans_by_duration(cls, plans: list[Any]) -> list[DurationGroup]:
        groups: "OrderedDict[str, list[Any]]" = OrderedDict()
        seconds_by_key: dict[str, int | None] = {}
        # Deterministic order: finite durations first, unlimited last.
        sorted_plans = sorted(
            plans,
            key=lambda plan: (
                getattr(plan, "time_limit_seconds", None) is None,
                int(getattr(plan, "time_limit_seconds", 0) or 0),
                int(getattr(plan, "id", 0) or 0),
            ),
        )
        for plan in sorted_plans:
            raw = getattr(plan, "time_limit_seconds", None)
            seconds = int(raw) if raw is not None and int(raw) > 0 else None
            key = "unlimited" if seconds is None else str(seconds)
            groups.setdefault(key, []).append(plan)
            seconds_by_key[key] = seconds
        return [
            DurationGroup(
                key=key,
                seconds=seconds_by_key[key],
                label=cls.duration_label(seconds_by_key[key]),
                plans=tuple(items),
            )
            for key, items in groups.items()
        ]

    async def get_service_by_catalog_id(self, catalog_id: int) -> RebeccaServiceRecord | None:
        services = await list_services(enabled_only=True)
        return next((item for item in services if int(item.id) == int(catalog_id)), None)

    async def get_active_service(self, service_id: int) -> RebeccaServiceRecord | None:
        item = await get_service_by_rebecca_id(int(service_id))
        if item and item.is_enabled:
            return item
        return None

    async def trial_access(self, service_id: int, trial_type: str) -> bool:
        if trial_type not in {"panel", "config"}:
            return False
        await self.ensure_schema()
        column = "panel_enabled" if trial_type == "panel" else "config_enabled"
        async with aiosqlite.connect(self.db_path) as conn:
            async with conn.execute(
                f"SELECT {column} FROM service_trial_access WHERE rebecca_service_id=?",
                (int(service_id),),
            ) as cur:
                row = await cur.fetchone()
        return True if row is None else bool(row[0])

    async def set_trial_access(self, service_id: int, trial_type: str, enabled: bool) -> None:
        if trial_type not in {"panel", "config"}:
            raise OperationsError("نوع تست نامعتبر است.")
        service = await self.get_active_service(int(service_id))
        if not service:
            raise OperationsError("سرویس Rebecca فعال نیست.")
        await self.ensure_schema()
        column = "panel_enabled" if trial_type == "panel" else "config_enabled"
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                "INSERT OR IGNORE INTO service_trial_access(rebecca_service_id) VALUES(?)",
                (int(service_id),),
            )
            await conn.execute(
                f"UPDATE service_trial_access SET {column}=?,updated_at=CURRENT_TIMESTAMP WHERE rebecca_service_id=?",
                (1 if enabled else 0, int(service_id)),
            )
            await conn.commit()

    async def trial_services(self, trial_type: str) -> list[RebeccaServiceRecord]:
        result: list[RebeccaServiceRecord] = []
        for service in await list_services(enabled_only=True):
            if await self.trial_access(service.rebecca_service_id, trial_type):
                result.append(service)
        return result

    @staticmethod
    def _config_trial_username(user_id: int) -> str:
        return f"test_{int(user_id)}_{secrets.token_hex(3)}"[:32]

    async def issue_config_trial_for_service(self, user_id: int, service_id: int) -> dict[str, Any]:
        if str(config.PANEL_PROVIDER or "").lower() != "rebecca":
            raise OperationsError("این مسیر تست برای Rebecca طراحی شده است.")
        service = await self.get_active_service(int(service_id))
        if not service:
            raise OperationsError("سرویس انتخاب‌شده فعال نیست.")
        if not await self.trial_access(service.rebecca_service_id, "config"):
            raise OperationsError("تست کانفیگ برای این سرویس غیرفعال است.")
        settings = await operations_service.get_trial_settings()
        if not settings["enabled"]:
            raise OperationsError("تست رایگان کانفیگ فعلاً غیرفعال است.")
        wait = await operations_service.trial_wait_seconds(int(user_id))
        if wait > 0:
            hours = max(1, math.ceil(wait / 3600))
            raise OperationsError(f"برای دریافت تست بعدی حدود {hours} ساعت صبر کنید.")

        username = self._config_trial_username(int(user_id))
        expire_at = int(time.time()) + int(settings["duration_seconds"])
        data_limit = int(settings["traffic_bytes"])
        payload = {
            "username": username,
            "status": "active",
            "expire": expire_at,
            "data_limit": data_limit,
            "ip_limit": None,
            "data_limit_reset_strategy": "no_reset",
            "on_hold_expire_duration": None,
            "note": f"Wingmarz free config trial / service {service.rebecca_service_id}",
            "telegram_id": str(int(user_id)),
            "contact_number": "",
            "flow": "",
            "service_id": int(service.rebecca_service_id),
            "auto_delete_in_days": 1,
        }
        try:
            result = await rebecca_api._request("POST", "/api/user", json=payload)
        except RebeccaAPIError as exc:
            raise OperationsError("ساخت کانفیگ تست در Rebecca ناموفق بود.") from exc
        if not isinstance(result, dict) or str(result.get("username", "")) != username:
            raise OperationsError("پاسخ Rebecca برای کانفیگ تست معتبر نبود.")

        subscription_url = str(
            result.get("subscription_url") or result.get("key_subscription_url") or ""
        ).strip() or None
        links: list[str] = []
        if isinstance(result.get("subscription_urls"), dict):
            links.extend(str(value) for value in result["subscription_urls"].values() if value)
        if isinstance(result.get("links"), list):
            links.extend(str(value) for value in result["links"] if value)
        unique_links: list[str] = []
        for item in [subscription_url, *links]:
            if item and item not in unique_links:
                unique_links.append(item)

        await operations_service.record_trial(
            user_id=int(user_id),
            provider="rebecca",
            provider_username=username,
            service_id=int(service.rebecca_service_id),
            subscription_url=subscription_url,
            expire_at=expire_at,
        )
        return {
            "provider": "rebecca",
            "service_id": int(service.rebecca_service_id),
            "service_name": str(service.display_name),
            "username": username,
            "subscription_url": subscription_url,
            "links": unique_links,
            "expire_at": expire_at,
            "traffic_bytes": data_limit,
        }

    async def issue_panel_trial_for_service(
        self,
        *,
        user_id: int,
        service_id: int,
        requested_username: str,
        telegram_username: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
    ) -> dict[str, Any]:
        if str(config.PANEL_PROVIDER or "").lower() != "rebecca":
            raise OperationsError("این مسیر تست پنل برای Rebecca طراحی شده است.")
        service = await self.get_active_service(int(service_id))
        if not service:
            raise OperationsError("سرویس انتخاب‌شده فعال نیست.")
        if not await self.trial_access(service.rebecca_service_id, "panel"):
            raise OperationsError("تست پنل برای این سرویس غیرفعال است.")
        settings = await trial_experience_service.get_panel_trial_settings()
        if not settings["enabled"]:
            raise OperationsError("تست پنل فعلاً غیرفعال است.")
        wait = await trial_experience_service.panel_trial_wait_seconds(int(user_id))
        if wait > 0:
            hours = max(1, math.ceil(wait / 3600))
            raise OperationsError(f"برای دریافت تست پنل بعدی حدود {hours} ساعت صبر کنید.")

        username = trial_experience_service.validate_username(requested_username)
        if not await trial_experience_service.username_available(username):
            raise OperationsError("این نام کاربری قبلاً استفاده شده است؛ یک نام دیگر انتخاب کنید.")
        password = secrets.token_urlsafe(18)
        expire_at = int(time.time()) + int(settings["duration_seconds"])
        try:
            await rebecca_api.create_admin_verified(
                username,
                password,
                int(user_id),
                data_limit=int(settings["traffic_bytes"]),
                expire=expire_at,
                users_limit=int(settings["max_users"]),
                services=[int(service.rebecca_service_id)],
            )
        except RebeccaConflict as exc:
            raise OperationsError("این نام کاربری هم‌زمان در Rebecca استفاده شد؛ نام دیگری انتخاب کنید.") from exc
        except RebeccaAPIError as exc:
            raise OperationsError("ساخت پنل تست در Rebecca ناموفق بود.") from exc

        admin = AdminModel(
            user_id=int(user_id),
            admin_name=f"تست {service.display_name}",
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
            origin_plan_id=None,
            allow_incremental_renewal=False,
        )
        saved = await db.add_admin(admin)
        if not saved:
            local = await db.get_admin_by_marzban_username(username)
            if not local or int(local.user_id) != int(user_id):
                raise OperationsError("پنل ساخته شد اما ثبت محلی آن ناموفق بود؛ با پشتیبانی تماس بگیرید.")

        await trial_experience_service.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                """
                INSERT INTO panel_trial_issues(
                    user_id,provider,provider_username,plan_id,expire_at,created_at
                ) VALUES(?,?,?,?,?,?)
                """,
                (int(user_id), "rebecca", username, 0, expire_at, int(time.time())),
            )
            await conn.commit()

        login_url = await db.get_setting("global_login_url")
        if not login_url:
            login_url = getattr(config, "REBECCA_LOGIN_URL", "") or getattr(config, "REBECCA_URL", "")
        return {
            "provider": "rebecca",
            "service_id": int(service.rebecca_service_id),
            "service_name": str(service.display_name),
            "username": username,
            "password": password,
            "login_url": str(login_url or ""),
            "expire_at": expire_at,
            "traffic_bytes": int(settings["traffic_bytes"]),
            "max_users": int(settings["max_users"]),
        }


service_marketplace_service = ServiceMarketplaceService()
