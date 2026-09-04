from __future__ import annotations

from dataclasses import dataclass
from html import escape
import re
from typing import Any, Iterable

import aiosqlite
from aiogram.types import InlineKeyboardButton

import config


_KEY_RE = re.compile(r"^[a-z0-9_.-]{1,64}$")
_EMOJI_ID_RE = re.compile(r"^[0-9]{1,32}$")
_MAX_FALLBACK_LEN = 32
_MAX_SCOPE_LEN = 64
_MAX_IDENTITY_LEN = 512
_MAX_DISPLAY_LEN = 512


class StyleValidationError(ValueError):
    """Raised when untrusted style input is invalid."""


@dataclass(frozen=True)
class StyledEmoji:
    id: int
    key: str
    custom_emoji_id: str
    fallback_unicode: str
    enabled: bool


@dataclass(frozen=True)
class TextOverride:
    id: int
    scope: str
    raw_identity: str
    display_text: str


class StyleEngine:
    """Presentation-only style service.

    Business identifiers are never rewritten. Premium emoji IDs, fallbacks and
    visible aliases live in dedicated tables and are only applied while rendering.
    """

    def __init__(self, db_path: str | None = None):
        self._explicit_db_path = db_path
        self._loaded = False
        self._style_enabled = False
        self._emoji_by_key: dict[str, StyledEmoji] = {}
        self._emoji_by_id: dict[int, StyledEmoji] = {}
        self._overrides: dict[tuple[str, str], TextOverride] = {}
        self._overrides_by_id: dict[int, TextOverride] = {}

    @property
    def db_path(self) -> str:
        return self._explicit_db_path or config.DATABASE_PATH

    @staticmethod
    def validate_key(key: str) -> str:
        normalized = (key or "").strip().lower()
        if not _KEY_RE.fullmatch(normalized):
            raise StyleValidationError("کلید باید فقط شامل a-z، 0-9، نقطه، خط تیره یا آندرلاین باشد.")
        return normalized

    @staticmethod
    def validate_scope(scope: str) -> str:
        normalized = StyleEngine.validate_key(scope)
        if len(normalized) > _MAX_SCOPE_LEN:
            raise StyleValidationError("scope بیش از حد طولانی است.")
        return normalized

    @staticmethod
    def validate_custom_emoji_id(custom_emoji_id: Any) -> str:
        value = str(custom_emoji_id or "").strip()
        if not _EMOJI_ID_RE.fullmatch(value):
            raise StyleValidationError("custom_emoji_id نامعتبر است.")
        return value

    @staticmethod
    def validate_fallback(fallback: str) -> str:
        value = (fallback or "").strip()
        if not value or len(value) > _MAX_FALLBACK_LEN or "<" in value or ">" in value:
            raise StyleValidationError("Fallback باید کوتاه، غیرخالی و بدون HTML باشد.")
        return value

    @staticmethod
    def validate_identity(raw_identity: str) -> str:
        value = str(raw_identity or "").strip()
        if not value or len(value) > _MAX_IDENTITY_LEN:
            raise StyleValidationError("شناسه خام نامعتبر است.")
        return value

    @staticmethod
    def validate_display_text(display_text: str) -> str:
        value = str(display_text or "").strip()
        if not value or len(value) > _MAX_DISPLAY_LEN:
            raise StyleValidationError("متن نمایشی نامعتبر است.")
        return value

    async def ensure_schema(self) -> None:
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS styled_emojis (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    key TEXT NOT NULL UNIQUE,
                    custom_emoji_id TEXT NOT NULL,
                    fallback_unicode TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS styled_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS styled_text_overrides (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scope TEXT NOT NULL,
                    raw_identity TEXT NOT NULL,
                    display_text TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(scope, raw_identity)
                )
                """
            )
            await conn.execute(
                "INSERT OR IGNORE INTO styled_settings(key, value) VALUES('style_enabled', '0')"
            )
            await conn.commit()

    async def init(self) -> None:
        await self.ensure_schema()
        await self.reload_cache()

    def invalidate_cache(self) -> None:
        self._loaded = False
        self._emoji_by_key.clear()
        self._emoji_by_id.clear()
        self._overrides.clear()
        self._overrides_by_id.clear()

    async def reload_cache(self) -> None:
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                "SELECT id, key, custom_emoji_id, fallback_unicode, enabled FROM styled_emojis"
            ) as cur:
                emoji_rows = await cur.fetchall()
            async with conn.execute(
                "SELECT id, scope, raw_identity, display_text FROM styled_text_overrides"
            ) as cur:
                override_rows = await cur.fetchall()
            async with conn.execute(
                "SELECT value FROM styled_settings WHERE key='style_enabled'"
            ) as cur:
                setting = await cur.fetchone()

        emojis = [
            StyledEmoji(
                id=int(row["id"]),
                key=str(row["key"]),
                custom_emoji_id=str(row["custom_emoji_id"]),
                fallback_unicode=str(row["fallback_unicode"]),
                enabled=bool(row["enabled"]),
            )
            for row in emoji_rows
        ]
        overrides = [
            TextOverride(
                id=int(row["id"]),
                scope=str(row["scope"]),
                raw_identity=str(row["raw_identity"]),
                display_text=str(row["display_text"]),
            )
            for row in override_rows
        ]
        self._emoji_by_key = {item.key: item for item in emojis}
        self._emoji_by_id = {item.id: item for item in emojis}
        self._overrides = {(item.scope, item.raw_identity): item for item in overrides}
        self._overrides_by_id = {item.id: item for item in overrides}
        self._style_enabled = bool(setting and str(setting["value"]).lower() in {"1", "true", "yes", "on"})
        self._loaded = True

    async def _ensure_loaded(self) -> None:
        if not self._loaded:
            await self.reload_cache()

    async def is_enabled(self) -> bool:
        await self._ensure_loaded()
        return self._style_enabled

    async def set_enabled(self, enabled: bool) -> None:
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                """
                INSERT INTO styled_settings(key, value, updated_at)
                VALUES('style_enabled', ?, CURRENT_TIMESTAMP)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP
                """,
                ("1" if enabled else "0",),
            )
            await conn.commit()
        self.invalidate_cache()
        await self._ensure_loaded()

    async def list_emojis(self) -> list[StyledEmoji]:
        await self._ensure_loaded()
        return sorted(self._emoji_by_id.values(), key=lambda item: item.id)

    async def get_emoji(self, key: str) -> StyledEmoji | None:
        await self._ensure_loaded()
        try:
            normalized = self.validate_key(key)
        except StyleValidationError:
            return None
        return self._emoji_by_key.get(normalized)

    async def get_emoji_by_id(self, item_id: int) -> StyledEmoji | None:
        await self._ensure_loaded()
        return self._emoji_by_id.get(int(item_id))

    async def upsert_emoji(
        self,
        key: str,
        custom_emoji_id: str,
        fallback_unicode: str,
        *,
        enabled: bool = True,
    ) -> StyledEmoji:
        key = self.validate_key(key)
        custom_emoji_id = self.validate_custom_emoji_id(custom_emoji_id)
        fallback_unicode = self.validate_fallback(fallback_unicode)
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                """
                INSERT INTO styled_emojis(key, custom_emoji_id, fallback_unicode, enabled)
                VALUES(?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    custom_emoji_id=excluded.custom_emoji_id,
                    fallback_unicode=excluded.fallback_unicode,
                    enabled=excluded.enabled,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (key, custom_emoji_id, fallback_unicode, 1 if enabled else 0),
            )
            await conn.commit()
        self.invalidate_cache()
        await self._ensure_loaded()
        item = self._emoji_by_key.get(key)
        if item is None:
            raise RuntimeError("Emoji mapping was not persisted")
        return item

    async def set_emoji_enabled(self, item_id: int, enabled: bool) -> bool:
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            cur = await conn.execute(
                "UPDATE styled_emojis SET enabled=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (1 if enabled else 0, int(item_id)),
            )
            await conn.commit()
            changed = cur.rowcount > 0
        self.invalidate_cache()
        await self._ensure_loaded()
        return changed

    async def update_fallback(self, item_id: int, fallback_unicode: str) -> bool:
        fallback_unicode = self.validate_fallback(fallback_unicode)
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            cur = await conn.execute(
                "UPDATE styled_emojis SET fallback_unicode=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (fallback_unicode, int(item_id)),
            )
            await conn.commit()
            changed = cur.rowcount > 0
        self.invalidate_cache()
        await self._ensure_loaded()
        return changed

    async def remove_emoji(self, item_id: int) -> bool:
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            cur = await conn.execute("DELETE FROM styled_emojis WHERE id=?", (int(item_id),))
            await conn.commit()
            changed = cur.rowcount > 0
        self.invalidate_cache()
        await self._ensure_loaded()
        return changed

    async def render_emoji(self, key: str, fallback: str | None = None) -> str:
        await self._ensure_loaded()
        item = await self.get_emoji(key)
        fallback_text = fallback if fallback is not None else (item.fallback_unicode if item else "")
        if fallback_text:
            fallback_text = self.validate_fallback(fallback_text)
        safe_fallback = escape(fallback_text)
        if not self._style_enabled or not item or not item.enabled:
            return safe_fallback
        emoji_id = self.validate_custom_emoji_id(item.custom_emoji_id)
        return f'<tg-emoji emoji-id="{emoji_id}">{safe_fallback}</tg-emoji>'

    async def decorate_text(
        self,
        icon_key: str,
        text: str,
        *,
        fallback: str | None = None,
        escape_text: bool = True,
    ) -> str:
        icon = await self.render_emoji(icon_key, fallback=fallback)
        body = escape(str(text)) if escape_text else str(text)
        return f"{icon} {body}".strip()

    @staticmethod
    def inline_button_supports_custom_icon() -> bool:
        fields = getattr(InlineKeyboardButton, "model_fields", {}) or {}
        return "icon_custom_emoji_id" in fields

    async def styled_button(
        self,
        text: str,
        *,
        icon_key: str | None = None,
        fallback: str | None = None,
        **button_kwargs: Any,
    ) -> InlineKeyboardButton:
        """Build a button without altering any action/callback property."""
        rendered_text = str(text)
        item: StyledEmoji | None = None
        enabled = await self.is_enabled()
        if icon_key:
            item = await self.get_emoji(icon_key)

        if enabled and item and item.enabled and self.inline_button_supports_custom_icon():
            button_kwargs["icon_custom_emoji_id"] = self.validate_custom_emoji_id(item.custom_emoji_id)
        else:
            fallback_text = fallback if fallback is not None else (item.fallback_unicode if item else "")
            if fallback_text:
                fallback_text = self.validate_fallback(fallback_text)
                rendered_text = f"{fallback_text} {rendered_text}".strip()
        return InlineKeyboardButton(text=rendered_text, **button_kwargs)

    async def list_overrides(self) -> list[TextOverride]:
        await self._ensure_loaded()
        return sorted(self._overrides_by_id.values(), key=lambda item: item.id)

    async def get_override_by_id(self, item_id: int) -> TextOverride | None:
        await self._ensure_loaded()
        return self._overrides_by_id.get(int(item_id))

    async def set_text_override(self, scope: str, raw_identity: str, display_text: str) -> TextOverride:
        scope = self.validate_scope(scope)
        raw_identity = self.validate_identity(raw_identity)
        display_text = self.validate_display_text(display_text)
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                """
                INSERT INTO styled_text_overrides(scope, raw_identity, display_text)
                VALUES(?, ?, ?)
                ON CONFLICT(scope, raw_identity) DO UPDATE SET
                    display_text=excluded.display_text,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (scope, raw_identity, display_text),
            )
            await conn.commit()
        self.invalidate_cache()
        await self._ensure_loaded()
        item = self._overrides.get((scope, raw_identity))
        if item is None:
            raise RuntimeError("Text override was not persisted")
        return item

    async def remove_text_override(self, item_id: int) -> bool:
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            cur = await conn.execute("DELETE FROM styled_text_overrides WHERE id=?", (int(item_id),))
            await conn.commit()
            changed = cur.rowcount > 0
        self.invalidate_cache()
        await self._ensure_loaded()
        return changed

    async def resolve_visual_alias(self, scope: str, raw_identity: str) -> tuple[str, str]:
        """Return (raw identity, visible label) using exact matching only."""
        await self._ensure_loaded()
        scope = self.validate_scope(scope)
        raw = self.validate_identity(raw_identity)
        item = self._overrides.get((scope, raw))
        return raw, (item.display_text if item else raw)


def extract_single_custom_emoji_id(message: Any) -> str:
    """Extract exactly one Telegram custom_emoji entity from text or caption."""
    entities: list[Any] = []
    for attr in ("entities", "caption_entities"):
        value = getattr(message, attr, None)
        if value:
            entities.extend(list(value))

    found: list[str] = []
    for entity in entities:
        entity_type = getattr(entity, "type", None)
        entity_type = getattr(entity_type, "value", entity_type)
        if entity_type != "custom_emoji":
            continue
        custom_id = getattr(entity, "custom_emoji_id", None)
        if custom_id is None:
            raise StyleValidationError("Custom emoji entity فاقد custom_emoji_id است.")
        found.append(StyleEngine.validate_custom_emoji_id(custom_id))

    if len(found) != 1:
        raise StyleValidationError("پیام باید دقیقاً شامل یک Premium Custom Emoji باشد.")
    return found[0]


style_engine = StyleEngine()
