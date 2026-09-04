from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
import re
import secrets
import time
from typing import Any

import aiosqlite

import config


_DISCOUNT_CODE_RE = re.compile(r"^[A-Z0-9_-]{3,32}$")
_BASE_SUDO_IDS = tuple(dict.fromkeys(int(x) for x in config.SUDO_ADMINS))


class OperationsError(ValueError):
    pass


@dataclass(frozen=True)
class DiscountQuote:
    discount_id: int
    code: str
    original_price: int
    discount_amount: int
    final_price: int


class OperationsService:
    def __init__(self, db_path: str | None = None):
        self._db_path = db_path

    @property
    def db_path(self) -> str:
        return self._db_path or config.DATABASE_PATH

    @property
    def base_sudo_ids(self) -> tuple[int, ...]:
        return _BASE_SUDO_IDS

    async def ensure_schema(self) -> None:
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS discount_codes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    code TEXT NOT NULL UNIQUE,
                    kind TEXT NOT NULL CHECK(kind IN ('percent','fixed')),
                    value INTEGER NOT NULL,
                    min_order INTEGER NOT NULL DEFAULT 0,
                    max_uses INTEGER NOT NULL DEFAULT 0,
                    per_user_limit INTEGER NOT NULL DEFAULT 1,
                    expires_at INTEGER,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_by INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS discount_redemptions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    discount_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    order_id INTEGER NOT NULL UNIQUE,
                    original_price INTEGER NOT NULL,
                    discount_amount INTEGER NOT NULL,
                    final_price INTEGER NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE INDEX IF NOT EXISTS idx_discount_redemptions_discount
                    ON discount_redemptions(discount_id);
                CREATE INDEX IF NOT EXISTS idx_discount_redemptions_user
                    ON discount_redemptions(discount_id, user_id);

                CREATE TABLE IF NOT EXISTS bot_admins (
                    user_id INTEGER PRIMARY KEY,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    added_by INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS menu_settings (
                    callback_data TEXT PRIMARY KEY,
                    is_visible INTEGER NOT NULL DEFAULT 1,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS trial_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS trial_issues (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    provider TEXT NOT NULL,
                    provider_username TEXT NOT NULL,
                    service_id INTEGER,
                    subscription_url TEXT,
                    expire_at INTEGER,
                    created_at INTEGER NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_trial_issues_user_created
                    ON trial_issues(user_id, created_at DESC);
                """
            )
            defaults = {
                "enabled": "0",
                "traffic_bytes": str(1024 * 1024 * 1024),
                "duration_seconds": str(60 * 60),
                "cooldown_seconds": str(24 * 60 * 60),
                "rebecca_service_id": "",
            }
            for key, value in defaults.items():
                await conn.execute(
                    "INSERT OR IGNORE INTO trial_settings(key,value) VALUES(?,?)",
                    (key, value),
                )
            await conn.commit()

    @staticmethod
    def normalize_discount_code(code: str) -> str:
        value = str(code or "").strip().upper()
        if not _DISCOUNT_CODE_RE.fullmatch(value):
            raise OperationsError("کد باید ۳ تا ۳۲ کاراکتر و فقط شامل حروف انگلیسی، عدد، _ یا - باشد.")
        return value

    async def create_discount(
        self,
        *,
        code: str,
        kind: str,
        value: int,
        min_order: int = 0,
        max_uses: int = 0,
        per_user_limit: int = 1,
        expires_at: int | None = None,
        created_by: int | None = None,
    ) -> int:
        await self.ensure_schema()
        code = self.normalize_discount_code(code)
        if kind not in {"percent", "fixed"}:
            raise OperationsError("نوع تخفیف نامعتبر است.")
        value = int(value)
        min_order = max(0, int(min_order))
        max_uses = max(0, int(max_uses))
        per_user_limit = max(1, int(per_user_limit))
        if kind == "percent" and not (1 <= value <= 100):
            raise OperationsError("درصد تخفیف باید بین ۱ تا ۱۰۰ باشد.")
        if kind == "fixed" and value <= 0:
            raise OperationsError("مبلغ تخفیف باید بیشتر از صفر باشد.")
        if expires_at is not None and int(expires_at) <= int(time.time()):
            raise OperationsError("زمان انقضا باید در آینده باشد.")
        try:
            async with aiosqlite.connect(self.db_path) as conn:
                cur = await conn.execute(
                    """
                    INSERT INTO discount_codes(
                        code,kind,value,min_order,max_uses,per_user_limit,expires_at,created_by
                    ) VALUES(?,?,?,?,?,?,?,?)
                    """,
                    (
                        code,
                        kind,
                        value,
                        min_order,
                        max_uses,
                        per_user_limit,
                        int(expires_at) if expires_at else None,
                        created_by,
                    ),
                )
                await conn.commit()
                return int(cur.lastrowid)
        except aiosqlite.IntegrityError as exc:
            raise OperationsError("این کد تخفیف قبلاً ثبت شده است.") from exc

    async def list_discounts(self) -> list[dict[str, Any]]:
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute("SELECT * FROM discount_codes ORDER BY id DESC") as cur:
                rows = await cur.fetchall()
            result = []
            for row in rows:
                item = dict(row)
                async with conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM discount_redemptions r
                    JOIN orders o ON o.id=r.order_id
                    WHERE r.discount_id=? AND COALESCE(o.status,'pending') NOT IN ('rejected','cancelled')
                    """,
                    (item["id"],),
                ) as cur:
                    usage_row = await cur.fetchone()
                item["used_count"] = int((usage_row or [0])[0] or 0)
                result.append(item)
            return result

    async def get_discount(self, discount_id: int) -> dict[str, Any] | None:
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute("SELECT * FROM discount_codes WHERE id=?", (int(discount_id),)) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def set_discount_active(self, discount_id: int, active: bool) -> bool:
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            cur = await conn.execute(
                "UPDATE discount_codes SET is_active=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (1 if active else 0, int(discount_id)),
            )
            await conn.commit()
            return cur.rowcount > 0

    async def delete_discount(self, discount_id: int) -> bool:
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            async with conn.execute(
                "SELECT COUNT(*) FROM discount_redemptions WHERE discount_id=?",
                (int(discount_id),),
            ) as cur:
                count = int((await cur.fetchone())[0] or 0)
            if count:
                cur = await conn.execute(
                    "UPDATE discount_codes SET is_active=0,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (int(discount_id),),
                )
            else:
                cur = await conn.execute("DELETE FROM discount_codes WHERE id=?", (int(discount_id),))
            await conn.commit()
            return cur.rowcount > 0

    async def has_active_discounts(self, price: int = 0) -> bool:
        await self.ensure_schema()
        now = int(time.time())
        async with aiosqlite.connect(self.db_path) as conn:
            async with conn.execute(
                """
                SELECT 1 FROM discount_codes
                WHERE is_active=1
                  AND (expires_at IS NULL OR expires_at>?)
                  AND min_order<=?
                LIMIT 1
                """,
                (now, max(0, int(price))),
            ) as cur:
                return await cur.fetchone() is not None

    async def quote_discount(self, code: str, user_id: int, price: int) -> DiscountQuote:
        await self.ensure_schema()
        code = self.normalize_discount_code(code)
        price = max(0, int(price))
        now = int(time.time())
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute("SELECT * FROM discount_codes WHERE code=?", (code,)) as cur:
                row = await cur.fetchone()
            if not row or not bool(row["is_active"]):
                raise OperationsError("کد تخفیف معتبر یا فعال نیست.")
            if row["expires_at"] is not None and int(row["expires_at"]) <= now:
                raise OperationsError("این کد تخفیف منقضی شده است.")
            if price < int(row["min_order"] or 0):
                raise OperationsError("مبلغ سفارش برای این کد کافی نیست.")

            async with conn.execute(
                """
                SELECT COUNT(*)
                FROM discount_redemptions r
                JOIN orders o ON o.id=r.order_id
                WHERE r.discount_id=? AND COALESCE(o.status,'pending') NOT IN ('rejected','cancelled')
                """,
                (int(row["id"]),),
            ) as cur:
                total_used = int((await cur.fetchone())[0] or 0)
            max_uses = int(row["max_uses"] or 0)
            if max_uses and total_used >= max_uses:
                raise OperationsError("ظرفیت استفاده از این کد تمام شده است.")

            async with conn.execute(
                """
                SELECT COUNT(*)
                FROM discount_redemptions r
                JOIN orders o ON o.id=r.order_id
                WHERE r.discount_id=? AND r.user_id=?
                  AND COALESCE(o.status,'pending') NOT IN ('rejected','cancelled')
                """,
                (int(row["id"]), int(user_id)),
            ) as cur:
                user_used = int((await cur.fetchone())[0] or 0)
            if user_used >= int(row["per_user_limit"] or 1):
                raise OperationsError("سقف استفاده شما از این کد تمام شده است.")

            if row["kind"] == "percent":
                amount = (price * int(row["value"])) // 100
            else:
                amount = int(row["value"])
            amount = max(0, min(price, amount))
            return DiscountQuote(
                discount_id=int(row["id"]),
                code=str(row["code"]),
                original_price=price,
                discount_amount=amount,
                final_price=max(0, price - amount),
            )

    async def record_redemption(self, quote: DiscountQuote, user_id: int, order_id: int) -> None:
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                """
                INSERT INTO discount_redemptions(
                    discount_id,user_id,order_id,original_price,discount_amount,final_price
                ) VALUES(?,?,?,?,?,?)
                """,
                (
                    quote.discount_id,
                    int(user_id),
                    int(order_id),
                    quote.original_price,
                    quote.discount_amount,
                    quote.final_price,
                ),
            )
            await conn.commit()

    async def list_runtime_admins(self) -> list[dict[str, Any]]:
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute("SELECT * FROM bot_admins ORDER BY created_at DESC") as cur:
                return [dict(row) for row in await cur.fetchall()]

    async def add_runtime_admin(self, user_id: int, added_by: int) -> None:
        await self.ensure_schema()
        user_id = int(user_id)
        if user_id <= 0:
            raise OperationsError("User ID نامعتبر است.")
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                """
                INSERT INTO bot_admins(user_id,is_active,added_by)
                VALUES(?,1,?)
                ON CONFLICT(user_id) DO UPDATE SET
                    is_active=1,added_by=excluded.added_by,updated_at=CURRENT_TIMESTAMP
                """,
                (user_id, int(added_by)),
            )
            await conn.commit()
        await self.sync_runtime_admins()

    async def set_runtime_admin_active(self, user_id: int, active: bool) -> bool:
        await self.ensure_schema()
        user_id = int(user_id)
        if user_id in self.base_sudo_ids and not active:
            raise OperationsError("SUDO اصلی تعریف‌شده در سرور از داخل ربات قابل غیرفعال‌سازی نیست.")
        async with aiosqlite.connect(self.db_path) as conn:
            cur = await conn.execute(
                "UPDATE bot_admins SET is_active=?,updated_at=CURRENT_TIMESTAMP WHERE user_id=?",
                (1 if active else 0, user_id),
            )
            await conn.commit()
            changed = cur.rowcount > 0
        await self.sync_runtime_admins()
        return changed

    async def remove_runtime_admin(self, user_id: int) -> bool:
        await self.ensure_schema()
        user_id = int(user_id)
        if user_id in self.base_sudo_ids:
            raise OperationsError("SUDO اصلی تعریف‌شده در سرور قابل حذف نیست.")
        async with aiosqlite.connect(self.db_path) as conn:
            cur = await conn.execute("DELETE FROM bot_admins WHERE user_id=?", (user_id,))
            await conn.commit()
            changed = cur.rowcount > 0
        await self.sync_runtime_admins()
        return changed

    async def sync_runtime_admins(self) -> None:
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            async with conn.execute("SELECT user_id FROM bot_admins WHERE is_active=1") as cur:
                dynamic = [int(row[0]) for row in await cur.fetchall()]
        merged = list(dict.fromkeys([*self.base_sudo_ids, *dynamic]))
        if isinstance(config.SUDO_ADMINS, list):
            config.SUDO_ADMINS[:] = merged
        else:
            config.SUDO_ADMINS = merged

    async def set_menu_visible(self, callback_data: str, visible: bool) -> None:
        await self.ensure_schema()
        key = str(callback_data or "").strip()
        if not key or len(key) > 64:
            raise OperationsError("شناسه دکمه نامعتبر است.")
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                """
                INSERT INTO menu_settings(callback_data,is_visible)
                VALUES(?,?)
                ON CONFLICT(callback_data) DO UPDATE SET
                    is_visible=excluded.is_visible,updated_at=CURRENT_TIMESTAMP
                """,
                (key, 1 if visible else 0),
            )
            await conn.commit()

    async def menu_is_visible(self, callback_data: str) -> bool:
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            async with conn.execute(
                "SELECT is_visible FROM menu_settings WHERE callback_data=?",
                (str(callback_data),),
            ) as cur:
                row = await cur.fetchone()
        return True if row is None else bool(row[0])

    async def get_trial_settings(self) -> dict[str, Any]:
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            async with conn.execute("SELECT key,value FROM trial_settings") as cur:
                raw = {str(k): str(v or "") for k, v in await cur.fetchall()}
        def as_int(key: str, default: int) -> int:
            try:
                return int(raw.get(key, default))
            except (TypeError, ValueError):
                return default
        service_raw = raw.get("rebecca_service_id", "").strip()
        return {
            "enabled": raw.get("enabled", "0") == "1",
            "traffic_bytes": max(1, as_int("traffic_bytes", 1024**3)),
            "duration_seconds": max(60, as_int("duration_seconds", 3600)),
            "cooldown_seconds": max(0, as_int("cooldown_seconds", 86400)),
            "rebecca_service_id": int(service_raw) if service_raw.isdigit() and int(service_raw) > 0 else None,
        }

    async def set_trial_setting(self, key: str, value: str | int | bool | None) -> None:
        allowed = {"enabled", "traffic_bytes", "duration_seconds", "cooldown_seconds", "rebecca_service_id"}
        if key not in allowed:
            raise OperationsError("تنظیم تست ناشناخته است.")
        await self.ensure_schema()
        if isinstance(value, bool):
            raw = "1" if value else "0"
        elif value is None:
            raw = ""
        else:
            raw = str(value)
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                """
                INSERT INTO trial_settings(key,value,updated_at)
                VALUES(?,?,CURRENT_TIMESTAMP)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=CURRENT_TIMESTAMP
                """,
                (key, raw),
            )
            await conn.commit()

    async def trial_wait_seconds(self, user_id: int) -> int:
        settings = await self.get_trial_settings()
        cooldown = int(settings["cooldown_seconds"])
        if cooldown <= 0:
            return 0
        async with aiosqlite.connect(self.db_path) as conn:
            async with conn.execute(
                "SELECT created_at FROM trial_issues WHERE user_id=? ORDER BY id DESC LIMIT 1",
                (int(user_id),),
            ) as cur:
                row = await cur.fetchone()
        if not row:
            return 0
        remaining = int(row[0]) + cooldown - int(time.time())
        return max(0, remaining)

    async def record_trial(
        self,
        *,
        user_id: int,
        provider: str,
        provider_username: str,
        service_id: int | None,
        subscription_url: str | None,
        expire_at: int,
    ) -> None:
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                """
                INSERT INTO trial_issues(
                    user_id,provider,provider_username,service_id,subscription_url,expire_at,created_at
                ) VALUES(?,?,?,?,?,?,?)
                """,
                (
                    int(user_id),
                    provider,
                    provider_username,
                    service_id,
                    subscription_url,
                    int(expire_at),
                    int(time.time()),
                ),
            )
            await conn.commit()

    async def _default_rebecca_service_id(self) -> int | None:
        settings = await self.get_trial_settings()
        if settings["rebecca_service_id"]:
            return int(settings["rebecca_service_id"])
        async with aiosqlite.connect(self.db_path) as conn:
            try:
                async with conn.execute(
                    "SELECT service_id FROM rebecca_services WHERE is_active=1 ORDER BY id ASC LIMIT 1"
                ) as cur:
                    row = await cur.fetchone()
                    return int(row[0]) if row and int(row[0]) > 0 else None
            except aiosqlite.OperationalError:
                return None

    @staticmethod
    def _trial_username(user_id: int) -> str:
        suffix = secrets.token_hex(3)
        raw = f"test_{int(user_id)}_{suffix}"
        return raw[:32]

    async def issue_trial(self, user_id: int) -> dict[str, Any]:
        settings = await self.get_trial_settings()
        if not settings["enabled"]:
            raise OperationsError("کانفیگ تست فعلاً غیرفعال است.")
        wait = await self.trial_wait_seconds(user_id)
        if wait > 0:
            hours = max(1, math.ceil(wait / 3600))
            raise OperationsError(f"برای دریافت تست بعدی حدود {hours} ساعت صبر کنید.")

        provider = str(config.PANEL_PROVIDER or "marzban").lower()
        username = self._trial_username(user_id)
        now = int(time.time())
        expire_at = now + int(settings["duration_seconds"])
        data_limit = int(settings["traffic_bytes"])
        service_id: int | None = None
        subscription_url: str | None = None
        links: list[str] = []

        if provider == "rebecca":
            from rebecca_api import rebecca_api, RebeccaAPIError

            service_id = await self._default_rebecca_service_id()
            if not service_id:
                raise OperationsError("برای کانفیگ تست Rebecca هنوز سرویس فعالی انتخاب نشده است.")
            payload = {
                "username": username,
                "status": "active",
                "expire": expire_at,
                "data_limit": data_limit,
                "ip_limit": None,
                "data_limit_reset_strategy": "no_reset",
                "on_hold_expire_duration": None,
                "note": "Wingmarz trial",
                "telegram_id": str(int(user_id)),
                "contact_number": "",
                "flow": "",
                "service_id": service_id,
                "auto_delete_in_days": 1,
            }
            try:
                result = await rebecca_api._request("POST", "/api/user", json=payload)
            except RebeccaAPIError as exc:
                raise OperationsError("ساخت کانفیگ تست در Rebecca ناموفق بود.") from exc
            if not isinstance(result, dict) or str(result.get("username", "")) != username:
                raise OperationsError("پاسخ Rebecca برای کانفیگ تست معتبر نبود.")
            subscription_url = str(
                result.get("subscription_url")
                or result.get("key_subscription_url")
                or ""
            ).strip() or None
            if isinstance(result.get("subscription_urls"), dict):
                links.extend(str(v) for v in result["subscription_urls"].values() if v)
            if isinstance(result.get("links"), list):
                links.extend(str(v) for v in result["links"] if v)

        elif provider == "marzban":
            from marzban_api import marzban_api

            try:
                inbound_response = await marzban_api._request(
                    "GET", f"{marzban_api.base_url}/api/inbounds"
                )
            except Exception as exc:
                raise OperationsError("دریافت inboundهای مرزبان ناموفق بود.") from exc
            if inbound_response.status_code != 200:
                raise OperationsError("مرزبان لیست inbound معتبر برنگرداند.")
            try:
                inbound_data = inbound_response.json()
            except ValueError as exc:
                raise OperationsError("پاسخ inbound مرزبان معتبر نبود.") from exc
            selected_protocol = None
            selected_tag = None
            if isinstance(inbound_data, dict):
                for protocol in ("vless", "vmess", "trojan", "shadowsocks"):
                    items = inbound_data.get(protocol)
                    if not isinstance(items, list) or not items:
                        continue
                    first = items[0]
                    tag = first.get("tag") if isinstance(first, dict) else None
                    if tag:
                        selected_protocol = protocol
                        selected_tag = str(tag)
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
                "note": "Wingmarz trial",
            }
            try:
                create_response = await marzban_api._request(
                    "POST", f"{marzban_api.base_url}/api/user", json=payload
                )
            except Exception as exc:
                raise OperationsError("ساخت کانفیگ تست در مرزبان ناموفق بود.") from exc
            if create_response.status_code not in (200, 201):
                raise OperationsError("مرزبان کانفیگ تست را ایجاد نکرد.")
            try:
                result = create_response.json()
            except ValueError as exc:
                raise OperationsError("پاسخ ساخت کاربر مرزبان معتبر نبود.") from exc
            if not isinstance(result, dict) or str(result.get("username", "")) != username:
                raise OperationsError("پاسخ مرزبان برای کانفیگ تست معتبر نبود.")
            subscription_url = str(result.get("subscription_url") or "").strip() or None
            if isinstance(result.get("links"), list):
                links.extend(str(v) for v in result["links"] if v)
        else:
            raise OperationsError("Provider فعلی برای کانفیگ تست پشتیبانی نمی‌شود.")

        await self.record_trial(
            user_id=user_id,
            provider=provider,
            provider_username=username,
            service_id=service_id,
            subscription_url=subscription_url,
            expire_at=expire_at,
        )
        unique_links = []
        for value in [subscription_url, *links]:
            if value and value not in unique_links:
                unique_links.append(value)
        return {
            "provider": provider,
            "username": username,
            "subscription_url": subscription_url,
            "links": unique_links,
            "expire_at": expire_at,
            "traffic_bytes": data_limit,
            "service_id": service_id,
        }

    @staticmethod
    def format_timestamp(timestamp: int | None) -> str:
        if not timestamp:
            return "بدون انقضا"
        return datetime.fromtimestamp(int(timestamp), tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


operations_service = OperationsService()
