from __future__ import annotations

from datetime import datetime, timezone
from html import escape
import json
import logging
import re
from typing import Any

import aiosqlite
from aiogram.exceptions import TelegramBadRequest

import config


logger = logging.getLogger(__name__)

TOPICS: dict[str, str] = {
    "starts": "👋 استارت‌ها",
    "admins": "👥 ادمین‌ها",
    "orders": "🛒 سفارش‌ها",
    "receipts": "🧾 رسیدهای پرداخت",
    "payments": "💳 وضعیت پرداخت‌ها",
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

    async def _upsert_topic(self, topic_key: str, title: str, thread_id: int) -> None:
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

    async def ensure_topic(self, bot, topic_key: str, *, recreate: bool = False) -> tuple[int, int] | None:
        """Return a live Group Ops target, creating/replacing the topic when needed."""
        cfg = await self.get_config()
        if not cfg or not int(cfg.get("enabled") or 0):
            return None
        if topic_key not in TOPICS:
            raise ValueError(f"Unknown Group Ops topic: {topic_key}")

        chat_id = int(cfg["chat_id"])
        topics = await self.topics()
        current = topics.get(topic_key)
        if current and not recreate:
            return chat_id, int(current["thread_id"])

        created = await bot.create_forum_topic(chat_id=chat_id, name=TOPICS[topic_key])
        thread_id = int(created.message_thread_id)
        await self._upsert_topic(topic_key, TOPICS[topic_key], thread_id)
        logger.info("Group Ops topic ready: %s -> %s", topic_key, thread_id)
        return chat_id, thread_id

    @staticmethod
    def _is_stale_topic_error(exc: Exception) -> bool:
        if not isinstance(exc, TelegramBadRequest):
            return False
        value = str(exc).lower()
        return any(token in value for token in (
            "message thread not found",
            "message_thread_id",
            "topic closed",
            "topic_deleted",
            "topic deleted",
        ))

    async def _fallback_text(self, bot, chat_id: int, topic_key: str, text: str, reply_markup=None) -> bool:
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=f"⚠️ <b>{escape(TOPICS.get(topic_key, topic_key))}</b>\n\n{text}",
                reply_markup=reply_markup,
            )
            return True
        except Exception as exc:
            logger.error("Group Ops fallback text failed (%s): %s", topic_key, exc)
            return False

    async def _fallback_photo(self, bot, chat_id: int, topic_key: str, file_id: str, caption: str, reply_markup=None) -> bool:
        try:
            await bot.send_photo(
                chat_id=chat_id,
                photo=file_id,
                caption=f"⚠️ <b>{escape(TOPICS.get(topic_key, topic_key))}</b>\n\n{caption}",
                reply_markup=reply_markup,
            )
            return True
        except Exception as exc:
            logger.error("Group Ops fallback photo failed (%s): %s", topic_key, exc)
            return False

    async def send_text(self, bot, topic_key: str, text: str, *, reply_markup=None) -> bool:
        target = await self.ensure_topic(bot, topic_key)
        if not target:
            logger.warning("Group Ops is not configured; dropped text event for %s", topic_key)
            return False
        chat_id, thread_id = target
        try:
            await bot.send_message(
                chat_id=chat_id,
                message_thread_id=thread_id,
                text=text,
                reply_markup=reply_markup,
            )
            return True
        except Exception as exc:
            logger.warning("Group Ops send_text failed (%s/%s): %s", topic_key, thread_id, exc)
            if self._is_stale_topic_error(exc):
                try:
                    chat_id, thread_id = await self.ensure_topic(bot, topic_key, recreate=True)
                    await bot.send_message(
                        chat_id=chat_id,
                        message_thread_id=thread_id,
                        text=text,
                        reply_markup=reply_markup,
                    )
                    return True
                except Exception as retry_exc:
                    logger.warning("Group Ops topic repair failed (%s): %s", topic_key, retry_exc)
            return await self._fallback_text(bot, chat_id, topic_key, text, reply_markup)

    async def send_photo(self, bot, topic_key: str, file_id: str, *, caption: str, reply_markup=None) -> bool:
        target = await self.ensure_topic(bot, topic_key)
        if not target:
            logger.warning("Group Ops is not configured; dropped photo event for %s", topic_key)
            return False
        chat_id, thread_id = target
        try:
            await bot.send_photo(
                chat_id=chat_id,
                message_thread_id=thread_id,
                photo=file_id,
                caption=caption,
                reply_markup=reply_markup,
            )
            return True
        except Exception as exc:
            logger.warning("Group Ops send_photo failed (%s/%s): %s", topic_key, thread_id, exc)
            if self._is_stale_topic_error(exc):
                try:
                    chat_id, thread_id = await self.ensure_topic(bot, topic_key, recreate=True)
                    await bot.send_photo(
                        chat_id=chat_id,
                        message_thread_id=thread_id,
                        photo=file_id,
                        caption=caption,
                        reply_markup=reply_markup,
                    )
                    return True
                except Exception as retry_exc:
                    logger.warning("Group Ops topic repair failed (%s): %s", topic_key, retry_exc)
            return await self._fallback_photo(bot, chat_id, topic_key, file_id, caption, reply_markup)

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
        traceback_text: str | None = None,
    ) -> bool:
        context_line = f"\n📍 {escape(context)}" if context else ""
        trace = self.redact_error(traceback_text or "")
        trace_block = f"\n\n<pre>{escape(trace[-2500:])}</pre>" if trace else ""
        return await self.send_text(
            bot,
            "errors",
            (
                "🐞 <b>خطای Wingmarz</b>"
                f"{context_line}\n\n"
                f"<code>{escape(self.redact_error(repr(exc)))}</code>"
                f"{trace_block}"
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
