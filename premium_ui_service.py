from __future__ import annotations

from dataclasses import dataclass
import re
from types import MethodType
from typing import Any

import aiosqlite

import config
from style_engine import style_engine


_EMOJI_TOKEN_RE = re.compile(r"\{emoji:([a-z0-9_.-]{1,64})\}")
_MAX_BUTTON_TEXT = 64
_MAX_MESSAGE_BODY = 3500


class PremiumUIError(ValueError):
    pass


@dataclass(frozen=True)
class ButtonCatalogItem:
    id: int
    callback_data: str
    default_text: str
    default_icon_key: str | None
    default_fallback: str | None
    display_text: str | None
    emoji_key: str | None


@dataclass(frozen=True)
class MessageTemplateItem:
    key: str
    default_body: str
    body: str
    is_overridden: bool


class PremiumUIService:
    """Runtime presentation management for buttons and system text templates.

    This service never rewrites callback_data or business IDs. It only changes
    visible button labels/icons and outgoing text rendering.
    """

    def __init__(self, db_path: str | None = None):
        self._explicit_db_path = db_path
        self._patched = False
        self._original_styled_button = None
        self._catalog_seen: set[str] = set()
        self._button_overrides: dict[str, tuple[str | None, str | None]] = {}
        self._base_messages = dict(config.MESSAGES)

    @property
    def db_path(self) -> str:
        return self._explicit_db_path or config.DATABASE_PATH

    async def ensure_schema(self) -> None:
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS styled_button_catalog (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    callback_data TEXT NOT NULL UNIQUE,
                    default_text TEXT NOT NULL,
                    default_icon_key TEXT,
                    default_fallback TEXT,
                    last_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS styled_button_overrides (
                    callback_data TEXT PRIMARY KEY,
                    display_text TEXT,
                    emoji_key TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS styled_message_overrides (
                    message_key TEXT PRIMARY KEY,
                    body TEXT NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
            await conn.commit()

    async def init(self) -> None:
        await self.ensure_schema()
        await self.reload_button_overrides()
        await self.apply_message_overrides()
        self.patch_styled_buttons()

    @staticmethod
    def validate_button_text(value: str) -> str:
        text = str(value or "").strip()
        if not text or len(text) > _MAX_BUTTON_TEXT or "\n" in text:
            raise PremiumUIError("متن دکمه باید یک‌خطی و حداکثر ۶۴ کاراکتر باشد.")
        if "<" in text or ">" in text:
            raise PremiumUIError("متن دکمه نباید HTML داشته باشد.")
        return text

    @staticmethod
    def validate_message_body(value: str) -> str:
        body = str(value or "").strip()
        if not body:
            raise PremiumUIError("متن پیام نمی‌تواند خالی باشد.")
        if len(body) > _MAX_MESSAGE_BODY:
            raise PremiumUIError("متن پیام بیش از حد طولانی است.")
        for match in re.finditer(r"\{emoji:([^}]+)\}", body):
            key = match.group(1)
            if not re.fullmatch(r"[a-z0-9_.-]{1,64}", key):
                raise PremiumUIError(f"کلید ایموجی نامعتبر است: {key}")
        return body

    async def reload_button_overrides(self) -> None:
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            async with conn.execute(
                "SELECT callback_data, display_text, emoji_key FROM styled_button_overrides"
            ) as cur:
                rows = await cur.fetchall()
        self._button_overrides = {
            str(callback): (
                str(display) if display is not None else None,
                str(emoji) if emoji is not None else None,
            )
            for callback, display, emoji in rows
        }

    async def apply_message_overrides(self) -> None:
        await self.ensure_schema()
        # Restore defaults first so deleted overrides also take effect after reload.
        for key, value in self._base_messages.items():
            config.MESSAGES[key] = value
        async with aiosqlite.connect(self.db_path) as conn:
            async with conn.execute("SELECT message_key, body FROM styled_message_overrides") as cur:
                rows = await cur.fetchall()
        for key, body in rows:
            key = str(key)
            if key in self._base_messages:
                config.MESSAGES[key] = str(body)

    async def catalog_button(
        self,
        callback_data: str,
        default_text: str,
        default_icon_key: str | None,
        default_fallback: str | None,
    ) -> None:
        callback_data = str(callback_data or "")
        if not callback_data or len(callback_data) > 512 or callback_data in self._catalog_seen:
            return
        self._catalog_seen.add(callback_data)
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                """
                INSERT INTO styled_button_catalog(
                    callback_data, default_text, default_icon_key, default_fallback, last_seen_at
                ) VALUES(?,?,?,?,CURRENT_TIMESTAMP)
                ON CONFLICT(callback_data) DO UPDATE SET
                    default_text=excluded.default_text,
                    default_icon_key=COALESCE(excluded.default_icon_key, styled_button_catalog.default_icon_key),
                    default_fallback=COALESCE(excluded.default_fallback, styled_button_catalog.default_fallback),
                    last_seen_at=CURRENT_TIMESTAMP
                """,
                (callback_data, str(default_text), default_icon_key, default_fallback),
            )
            await conn.commit()

    def patch_styled_buttons(self) -> None:
        if self._patched:
            return
        self._original_styled_button = style_engine.styled_button
        original = self._original_styled_button
        service = self

        async def wrapped(
            _engine,
            text: str,
            *,
            icon_key: str | None = None,
            fallback: str | None = None,
            **button_kwargs: Any,
        ):
            callback_data = button_kwargs.get("callback_data")
            rendered_text = str(text)
            rendered_icon = icon_key
            rendered_fallback = fallback
            if isinstance(callback_data, str) and callback_data:
                await service.catalog_button(callback_data, rendered_text, icon_key, fallback)
                override = service._button_overrides.get(callback_data)
                if override:
                    display_text, emoji_key = override
                    if display_text:
                        rendered_text = display_text
                    if emoji_key:
                        rendered_icon = emoji_key
                        # Let the selected emoji's own Unicode fallback be used.
                        rendered_fallback = None
            return await original(
                rendered_text,
                icon_key=rendered_icon,
                fallback=rendered_fallback,
                **button_kwargs,
            )

        style_engine.styled_button = MethodType(wrapped, style_engine)
        self._patched = True

    async def list_buttons(self, page: int = 0, page_size: int = 12) -> tuple[list[ButtonCatalogItem], int]:
        await self.ensure_schema()
        page_size = max(1, min(20, int(page_size)))
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute("SELECT COUNT(*) FROM styled_button_catalog") as cur:
                total = int((await cur.fetchone())[0] or 0)
            pages = max(1, (total + page_size - 1) // page_size)
            page = max(0, min(int(page), pages - 1))
            async with conn.execute(
                """
                SELECT c.id,c.callback_data,c.default_text,c.default_icon_key,c.default_fallback,
                       o.display_text,o.emoji_key
                FROM styled_button_catalog c
                LEFT JOIN styled_button_overrides o ON o.callback_data=c.callback_data
                ORDER BY c.last_seen_at DESC,c.id DESC
                LIMIT ? OFFSET ?
                """,
                (page_size, page * page_size),
            ) as cur:
                rows = await cur.fetchall()
        items = [
            ButtonCatalogItem(
                id=int(row["id"]),
                callback_data=str(row["callback_data"]),
                default_text=str(row["default_text"]),
                default_icon_key=str(row["default_icon_key"]) if row["default_icon_key"] else None,
                default_fallback=str(row["default_fallback"]) if row["default_fallback"] else None,
                display_text=str(row["display_text"]) if row["display_text"] else None,
                emoji_key=str(row["emoji_key"]) if row["emoji_key"] else None,
            )
            for row in rows
        ]
        return items, pages

    async def get_button(self, item_id: int) -> ButtonCatalogItem | None:
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                """
                SELECT c.id,c.callback_data,c.default_text,c.default_icon_key,c.default_fallback,
                       o.display_text,o.emoji_key
                FROM styled_button_catalog c
                LEFT JOIN styled_button_overrides o ON o.callback_data=c.callback_data
                WHERE c.id=?
                """,
                (int(item_id),),
            ) as cur:
                row = await cur.fetchone()
        if not row:
            return None
        return ButtonCatalogItem(
            id=int(row["id"]),
            callback_data=str(row["callback_data"]),
            default_text=str(row["default_text"]),
            default_icon_key=str(row["default_icon_key"]) if row["default_icon_key"] else None,
            default_fallback=str(row["default_fallback"]) if row["default_fallback"] else None,
            display_text=str(row["display_text"]) if row["display_text"] else None,
            emoji_key=str(row["emoji_key"]) if row["emoji_key"] else None,
        )

    async def _save_button_override(
        self,
        callback_data: str,
        display_text: str | None,
        emoji_key: str | None,
    ) -> None:
        await self.ensure_schema()
        if display_text is None and emoji_key is None:
            async with aiosqlite.connect(self.db_path) as conn:
                await conn.execute("DELETE FROM styled_button_overrides WHERE callback_data=?", (callback_data,))
                await conn.commit()
            self._button_overrides.pop(callback_data, None)
            return
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                """
                INSERT INTO styled_button_overrides(callback_data,display_text,emoji_key,updated_at)
                VALUES(?,?,?,CURRENT_TIMESTAMP)
                ON CONFLICT(callback_data) DO UPDATE SET
                    display_text=excluded.display_text,
                    emoji_key=excluded.emoji_key,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (callback_data, display_text, emoji_key),
            )
            await conn.commit()
        self._button_overrides[callback_data] = (display_text, emoji_key)

    async def set_button_text(self, item_id: int, text: str) -> None:
        item = await self.get_button(item_id)
        if not item:
            raise PremiumUIError("دکمه پیدا نشد.")
        text = self.validate_button_text(text)
        await self._save_button_override(item.callback_data, text, item.emoji_key)

    async def reset_button_text(self, item_id: int) -> None:
        item = await self.get_button(item_id)
        if not item:
            raise PremiumUIError("دکمه پیدا نشد.")
        await self._save_button_override(item.callback_data, None, item.emoji_key)

    async def set_button_emoji(self, item_id: int, emoji_key: str) -> None:
        item = await self.get_button(item_id)
        if not item:
            raise PremiumUIError("دکمه پیدا نشد.")
        emoji_key = style_engine.validate_key(emoji_key)
        if not await style_engine.get_emoji(emoji_key):
            raise PremiumUIError("این Premium Emoji ثبت نشده است.")
        await self._save_button_override(item.callback_data, item.display_text, emoji_key)

    async def reset_button_emoji(self, item_id: int) -> None:
        item = await self.get_button(item_id)
        if not item:
            raise PremiumUIError("دکمه پیدا نشد.")
        await self._save_button_override(item.callback_data, item.display_text, None)

    async def list_messages(self) -> list[MessageTemplateItem]:
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            async with conn.execute("SELECT message_key,body FROM styled_message_overrides") as cur:
                overrides = {str(k): str(v) for k, v in await cur.fetchall()}
        return [
            MessageTemplateItem(
                key=key,
                default_body=default,
                body=overrides.get(key, default),
                is_overridden=key in overrides,
            )
            for key, default in self._base_messages.items()
        ]

    async def get_message(self, key: str) -> MessageTemplateItem | None:
        if key not in self._base_messages:
            return None
        items = await self.list_messages()
        return next((item for item in items if item.key == key), None)

    async def set_message(self, key: str, body: str) -> None:
        if key not in self._base_messages:
            raise PremiumUIError("کلید پیام ناشناخته است.")
        body = self.validate_message_body(body)
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                """
                INSERT INTO styled_message_overrides(message_key,body,updated_at)
                VALUES(?,?,CURRENT_TIMESTAMP)
                ON CONFLICT(message_key) DO UPDATE SET body=excluded.body,updated_at=CURRENT_TIMESTAMP
                """,
                (key, body),
            )
            await conn.commit()
        config.MESSAGES[key] = body

    async def reset_message(self, key: str) -> None:
        if key not in self._base_messages:
            raise PremiumUIError("کلید پیام ناشناخته است.")
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute("DELETE FROM styled_message_overrides WHERE message_key=?", (key,))
            await conn.commit()
        config.MESSAGES[key] = self._base_messages[key]

    async def render_placeholders(self, text: str) -> str:
        """Replace {emoji:key} with Telegram premium emoji HTML.

        Unknown keys remain visible so a typo never silently removes content.
        """
        value = str(text)
        if "{emoji:" not in value:
            return value
        keys = list(dict.fromkeys(_EMOJI_TOKEN_RE.findall(value)))
        replacements: dict[str, str] = {}
        for key in keys:
            item = await style_engine.get_emoji(key)
            if not item:
                continue
            replacements[key] = await style_engine.render_emoji(key, fallback=item.fallback_unicode)
        if not replacements:
            return value

        def repl(match: re.Match[str]) -> str:
            key = match.group(1)
            return replacements.get(key, match.group(0))

        return _EMOJI_TOKEN_RE.sub(repl, value)


premium_ui_service = PremiumUIService()
