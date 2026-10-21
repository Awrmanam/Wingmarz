from __future__ import annotations

from datetime import datetime, timezone
from html import escape
import json
import logging
import re
from typing import Any

import aiosqlite

import config


logger = logging.getLogger(__name__)

TOPICS: dict[str, str] = {
    "starts": "👋 استارت‌ها",
    "admins": "👥 ادمین‌ها",
    "orders": "🛒 سفارش‌ها",
    "payments": "💳 پرداخت‌ها",
    "receipts": "🧾 رسیدهای پرداخت",
    "kyc": "🪪 احراز هویت",
    "unverified": "🟡 تاییدنشده‌ها",
    "errors": "🐞 خطاها",
    "daily": "📊 گزارش روزانه",
}


class GroupOpsService:
    def __init__(self, db_path: str | None = None) -> None:
        self._db_path = db_path

    @property
    def db_path(self) -> str:
        return self._db_path or config.DATABASE_PATH

    async def ensure_schema(self) -> None:
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS group_ops_config (
                    id INTEGER PRIMARY KEY CHECK(id=1),
                    chat_id INTEGER NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
                    created_by INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS group_ops_topics (
                    topic_key TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    thread_id INTEGER NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS group_ops_event_log (
                    event_key TEXT PRIMARY KEY,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS group_ops_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    actor_id INTEGER,
                    action TEXT NOT NULL,
                    subject_type TEXT,
                    subject_id TEXT,
                    old_value TEXT,
                    new_value TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS group_ops_alert_state (
                    admin_id INTEGER NOT NULL,
                    metric TEXT NOT NULL,
                    last_value REAL,
                    last_rank INTEGER NOT NULL DEFAULT 0,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(admin_id, metric)
                );

                CREATE INDEX IF NOT EXISTS idx_group_ops_audit_created
                ON group_ops_audit(created_at);
                """
            )
            await conn.commit()

    async def configure_group(self, chat_id: int, actor_id: int) -> None:
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            async with conn.execute("SELECT chat_id FROM group_ops_config WHERE id=1") as cur:
                previous = await cur.fetchone()
            # Telegram thread IDs are only meaningful inside their original chat.
            # Rebinding to a different forum must never reuse the old topic IDs.
            if previous and int(previous[0]) != int(chat_id):
                await conn.execute("DELETE FROM group_ops_topics")
            await conn.execute(
                """
                INSERT INTO group_ops_config(id,chat_id,enabled,created_by,updated_at)
                VALUES(1,?,1,?,CURRENT_TIMESTAMP)
                ON CONFLICT(id) DO UPDATE SET
                    chat_id=excluded.chat_id,
                    enabled=1,
                    created_by=excluded.created_by,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (int(chat_id), int(actor_id)),
            )
            await conn.commit()

    async def get_config(self) -> dict[str, Any] | None:
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute("SELECT * FROM group_ops_config WHERE id=1") as cur:
                row = await cur.fetchone()
        return dict(row) if row else None

    async def is_configured_chat(self, chat_id: int) -> bool:
        row = await self.get_config()
        return bool(row and int(row.get("enabled") or 0) == 1 and int(row["chat_id"]) == int(chat_id))

    async def setup_topics(self, bot, chat_id: int) -> dict[str, int]:
        await self.ensure_schema()
        existing = await self.topics()
        result: dict[str, int] = {}
        for key, title in TOPICS.items():
            if key in existing:
                result[key] = int(existing[key]["thread_id"])
                continue
            created = await bot.create_forum_topic(chat_id=int(chat_id), name=title)
            thread_id = int(created.message_thread_id)
            async with aiosqlite.connect(self.db_path) as conn:
                await conn.execute(
                    """
                    INSERT INTO group_ops_topics(topic_key,title,thread_id,updated_at)
                    VALUES(?,?,?,CURRENT_TIMESTAMP)
                    ON CONFLICT(topic_key) DO UPDATE SET
                        title=excluded.title,
                        thread_id=excluded.thread_id,
                        updated_at=CURRENT_TIMESTAMP
                    """,
                    (key, title, thread_id),
                )
                await conn.commit()
            result[key] = thread_id
        return result

    async def clear_topics(self) -> None:
        """Forget stored thread IDs so the configured forum can be rebuilt."""
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute("DELETE FROM group_ops_topics")
            await conn.commit()

    async def topics(self) -> dict[str, dict[str, Any]]:
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute("SELECT * FROM group_ops_topics") as cur:
                rows = await cur.fetchall()
        return {str(row["topic_key"]): dict(row) for row in rows}

    async def _store_topic(self, topic_key: str, title: str, thread_id: int) -> None:
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                """
                INSERT INTO group_ops_topics(topic_key,title,thread_id,updated_at)
                VALUES(?,?,?,CURRENT_TIMESTAMP)
                ON CONFLICT(topic_key) DO UPDATE SET
                    title=excluded.title,
                    thread_id=excluded.thread_id,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (str(topic_key), str(title), int(thread_id)),
            )
            await conn.commit()

    async def _delete_topic_state(self, topic_key: str) -> None:
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute("DELETE FROM group_ops_topics WHERE topic_key=?", (str(topic_key),))
            await conn.commit()

    async def _target(self, topic_key: str) -> tuple[int, int] | None:
        cfg = await self.get_config()
        if not cfg or not int(cfg.get("enabled") or 0):
            return None
        chat_id = int(cfg["chat_id"])
        topics = await self.topics()
        topic = topics.get(topic_key)
        if not topic:
            return chat_id, 0
        return chat_id, int(topic["thread_id"])

    async def _ensure_topic(self, bot, topic_key: str) -> tuple[int, int] | None:
        cfg = await self.get_config()
        if not cfg or not int(cfg.get("enabled") or 0):
            return None
        chat_id = int(cfg["chat_id"])
        title = TOPICS.get(topic_key)
        if not title:
            logger.warning("Unknown Group Ops topic key: %s", topic_key)
            return chat_id, 0

        topics = await self.topics()
        topic = topics.get(topic_key)
        if topic:
            return chat_id, int(topic["thread_id"])

        try:
            created = await bot.create_forum_topic(chat_id=chat_id, name=title)
            thread_id = int(created.message_thread_id)
            await self._store_topic(topic_key, title, thread_id)
            logger.info("Group Ops topic auto-created: %s -> %s", topic_key, thread_id)
            return chat_id, thread_id
        except Exception as exc:
            logger.warning("Group Ops topic auto-create failed (%s): %s", topic_key, exc)
            # Keep the operations channel useful even if topic creation is temporarily
            # unavailable. Sending without a thread lands in General.
            return chat_id, 0

    async def _repair_topic(self, bot, topic_key: str, chat_id: int) -> tuple[int, int]:
        title = TOPICS.get(topic_key)
        if not title:
            return chat_id, 0
        await self._delete_topic_state(topic_key)
        try:
            created = await bot.create_forum_topic(chat_id=chat_id, name=title)
            thread_id = int(created.message_thread_id)
            await self._store_topic(topic_key, title, thread_id)
            logger.warning("Group Ops topic repaired: %s -> %s", topic_key, thread_id)
            return chat_id, thread_id
        except Exception as exc:
            logger.warning("Group Ops topic repair failed (%s): %s", topic_key, exc)
            return chat_id, 0

    async def send_text(self, bot, topic_key: str, text: str, *, reply_markup=None) -> bool:
        target = await self._ensure_topic(bot, topic_key)
        if not target:
            logger.warning("Group Ops is not configured; dropping text event %s", topic_key)
            return False
        chat_id, thread_id = target

        async def _send(thread: int):
            kwargs = {
                "chat_id": chat_id,
                "text": text,
                "reply_markup": reply_markup,
            }
            if thread:
                kwargs["message_thread_id"] = thread
            return await bot.send_message(**kwargs)

        try:
            await _send(thread_id)
            return True
        except Exception as first_exc:
            logger.warning("Group ops send_text failed (%s/%s): %s", topic_key, thread_id, first_exc)
            repaired_chat, repaired_thread = await self._repair_topic(bot, topic_key, chat_id)
            try:
                await _send(repaired_thread)
                return True
            except Exception as second_exc:
                logger.error(
                    "Group ops send_text retry failed (%s/%s): %s",
                    topic_key, repaired_thread, second_exc,
                )
                return False

    async def send_photo(self, bot, topic_key: str, file_id: str, *, caption: str, reply_markup=None) -> bool:
        target = await self._ensure_topic(bot, topic_key)
        if not target:
            logger.warning("Group Ops is not configured; dropping photo event %s", topic_key)
            return False
        chat_id, thread_id = target

        async def _send(thread: int):
            kwargs = {
                "chat_id": chat_id,
                "photo": file_id,
                "caption": caption,
                "reply_markup": reply_markup,
            }
            if thread:
                kwargs["message_thread_id"] = thread
            return await bot.send_photo(**kwargs)

        try:
            await _send(thread_id)
            return True
        except Exception as first_exc:
            logger.warning("Group ops send_photo failed (%s/%s): %s", topic_key, thread_id, first_exc)
            repaired_chat, repaired_thread = await self._repair_topic(bot, topic_key, chat_id)
            try:
                await _send(repaired_thread)
                return True
            except Exception as second_exc:
                logger.error(
                    "Group ops send_photo retry failed (%s/%s): %s",
                    topic_key, repaired_thread, second_exc,
                )
                return False

    async def claim_event(self, event_key: str) -> bool:
        """Return True only once for a stable event key."""
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            cur = await conn.execute(
                "INSERT OR IGNORE INTO group_ops_event_log(event_key) VALUES(?)",
                (str(event_key),),
            )
            await conn.commit()
            return cur.rowcount == 1

    async def audit(
        self,
        *,
        actor_id: int | None,
        action: str,
        subject_type: str | None = None,
        subject_id: str | int | None = None,
        old_value: Any = None,
        new_value: Any = None,
    ) -> None:
        await self.ensure_schema()
        def dump(value: Any) -> str | None:
            if value is None:
                return None
            try:
                return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
            except Exception:
                return str(value)
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                """
                INSERT INTO group_ops_audit(
                    actor_id,action,subject_type,subject_id,old_value,new_value
                ) VALUES(?,?,?,?,?,?)
                """,
                (
                    int(actor_id) if actor_id is not None else None,
                    str(action),
                    subject_type,
                    str(subject_id) if subject_id is not None else None,
                    dump(old_value),
                    dump(new_value),
                ),
            )
            await conn.commit()

    async def get_alert_state(self, admin_id: int, metric: str) -> dict[str, Any] | None:
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                "SELECT * FROM group_ops_alert_state WHERE admin_id=? AND metric=?",
                (int(admin_id), str(metric)),
            ) as cur:
                row = await cur.fetchone()
        return dict(row) if row else None

    async def mark_alert_state(self, admin_id: int, metric: str, value: float, rank: int) -> None:
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                """
                INSERT INTO group_ops_alert_state(admin_id,metric,last_value,last_rank,updated_at)
                VALUES(?,?,?,?,CURRENT_TIMESTAMP)
                ON CONFLICT(admin_id,metric) DO UPDATE SET
                    last_value=excluded.last_value,
                    last_rank=excluded.last_rank,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (int(admin_id), str(metric), float(value), int(rank)),
            )
            await conn.commit()

    async def notify_start(self, bot, user, *, account_kind: str, kyc_status: str, trusted: bool) -> bool:
        username = f"@{escape(user.username)}" if getattr(user, "username", None) else "—"
        full_name = " ".join(
            x for x in [getattr(user, "first_name", None), getattr(user, "last_name", None)] if x
        ).strip() or "—"
        return await self.send_text(
            bot,
            "starts",
            (
                "👋 <b>/start جدید</b>\n\n"
                f"👤 نام: <b>{escape(full_name)}</b>\n"
                f"🔗 یوزرنیم: {username}\n"
                f"🆔 Telegram ID: <code>{int(user.id)}</code>\n"
                f"🏷 نوع: <b>{escape(account_kind)}</b>\n"
                f"🪪 KYC: <b>{escape(kyc_status)}</b>\n"
                f"🤝 Trust: <b>{'تأیید' if trusted else 'عادی'}</b>"
            ),
        )

    def redact_error(self, raw: str) -> str:
        text = str(raw or "")
        for secret in (
            getattr(config, "BOT_TOKEN", None),
            getattr(config, "REBECCA_BEARER_TOKEN", None),
            getattr(config, "MARZBAN_PASSWORD", None),
        ):
            if secret:
                text = text.replace(str(secret), "[REDACTED]")
        text = re.sub(r"(?i)(authorization|token|password|secret)\s*[:=]\s*[^\s,;]+", r"\1=[REDACTED]", text)
        text = re.sub(r"\b(?:\d[ -]*?){16}\b", "[CARD-REDACTED]", text)
        return text[:3500]

    async def report_error(
        self,
        bot,
        exc: BaseException,
        *,
        context: str | None = None,
        details: str | None = None,
    ) -> bool:
        context_line = f"\n📍 {escape(context)}" if context else ""
        detail_text = ""
        if details:
            redacted = self.redact_error(details)
            # Keep the useful tail where Python includes file/line/exception.
            detail_text = f"\n\n<pre>{escape(redacted[-2800:])}</pre>"
        return await self.send_text(
            bot,
            "errors",
            (
                "🐞 <b>خطای Wingmarz</b>"
                f"{context_line}\n\n"
                f"<code>{escape(self.redact_error(repr(exc)))}</code>"
                f"{detail_text}"
            ),
        )

    async def daily_summary(self, bot) -> bool:
        await self.ensure_schema()
        today = datetime.now(timezone.utc).date().isoformat()
        async with aiosqlite.connect(self.db_path) as conn:
            async def scalar(query: str, params=()):
                try:
                    async with conn.execute(query, params) as cur:
                        row = await cur.fetchone()
                    return int((row or [0])[0] or 0)
                except aiosqlite.OperationalError:
                    return 0
            orders = await scalar("SELECT COUNT(*) FROM orders WHERE date(created_at)=?", (today,))
            approved = await scalar("SELECT COUNT(*) FROM orders WHERE status='approved' AND date(created_at)=?", (today,))
            submitted = await scalar("SELECT COUNT(*) FROM orders WHERE status='submitted'")
            pending_kyc = await scalar("SELECT COUNT(*) FROM kyc_profiles WHERE status='pending'")
            active_admins = await scalar("SELECT COUNT(*) FROM admins WHERE is_active=1")
            revenue = await scalar("SELECT COALESCE(SUM(price_snapshot),0) FROM orders WHERE status='approved' AND date(updated_at)=?", (today,))
        return await self.send_text(
            bot,
            "daily",
            (
                "📊 <b>گزارش روزانه Wingmarz</b>\n\n"
                f"🛒 سفارش امروز: <b>{orders}</b>\n"
                f"✅ تأییدشده امروز: <b>{approved}</b>\n"
                f"🧾 منتظر بررسی: <b>{submitted}</b>\n"
                f"💵 فروش تأییدشده امروز: <b>{revenue:,}</b> تومان\n"
                f"🪪 KYC در انتظار: <b>{pending_kyc}</b>\n"
                f"👥 پنل فعال: <b>{active_admins}</b>"
            ),
        )


group_ops_service = GroupOpsService()
