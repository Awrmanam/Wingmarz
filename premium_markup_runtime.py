from __future__ import annotations

import re

from aiogram.types import InlineKeyboardMarkup

from premium_ui_service import premium_ui_service
from style_engine import style_engine


_BUTTON_EMOJI_TOKEN_RE = re.compile(r"\{emoji:([a-z0-9_.-]{1,64})\}")
_GENERIC_TOKEN_FALLBACK_PREFIXES = ("📦 ", "🔌 ", "🧪 ", "🧩 ", "🔘 ", "✨ ")


async def _consume_button_emoji_token(text: str, explicit_key: str | None) -> tuple[str, str | None]:
    """Turn a {emoji:key} token in button copy into Telegram's button icon field.

    Buttons cannot render tg-emoji HTML inside their text. Bot API 9.4 gives
    them one dedicated custom emoji icon, so the first known token becomes that
    icon and all occurrences of the same token are removed from visible text.
    Unknown tokens stay visible instead of disappearing silently.
    """
    value = str(text)
    selected_key = explicit_key
    matches = list(_BUTTON_EMOJI_TOKEN_RE.finditer(value))
    known_keys: set[str] = set()
    for match in matches:
        key = match.group(1)
        item = await style_engine.get_emoji(key)
        if item and item.enabled:
            known_keys.add(key)
            if selected_key is None:
                selected_key = key
    if not known_keys:
        return value, selected_key

    def repl(match: re.Match[str]) -> str:
        return "" if match.group(1) in known_keys else match.group(0)

    value = _BUTTON_EMOJI_TOKEN_RE.sub(repl, value)
    value = re.sub(r"\s{2,}", " ", value).strip()
    # A generic Unicode icon may already have been prepended by styled_button
    # before this runtime sees the markup. Once a real Premium button icon is
    # selected, keep only the custom icon so the button does not show two icons.
    for prefix in _GENERIC_TOKEN_FALLBACK_PREFIXES:
        if value.startswith(prefix):
            value = value[len(prefix):].strip()
            break
    return value, selected_key


async def style_reply_markup(markup):
    """Catalog and style callback buttons from any handler, including legacy ones.

    Presentation identity is callback_data + the original visible text, so two
    buttons may share one callback while keeping independent text/emoji choices.
    callback_data itself is never changed.
    """
    if not isinstance(markup, InlineKeyboardMarkup):
        return markup

    changed = False
    new_rows = []
    for row in markup.inline_keyboard:
        new_row = []
        for button in row:
            callback_data = getattr(button, "callback_data", None)
            if not isinstance(callback_data, str) or not callback_data:
                new_row.append(button)
                continue

            original_text = str(getattr(button, "text", ""))
            await premium_ui_service.catalog_button(callback_data, original_text, None, None)
            override = premium_ui_service.override_for(callback_data, original_text)
            display_text = override[0] if override else None
            emoji_key = override[1] if override else None
            text = display_text or original_text
            text, emoji_key = await _consume_button_emoji_token(text, emoji_key)

            updates = {}
            if emoji_key:
                item = await style_engine.get_emoji(emoji_key)
                if item and item.enabled:
                    if style_engine.inline_button_supports_custom_icon():
                        updates["icon_custom_emoji_id"] = item.custom_emoji_id
                    else:
                        fallback = item.fallback_unicode
                        if fallback and not text.startswith(fallback):
                            text = f"{fallback} {text}".strip()
            if text != original_text:
                updates["text"] = text
            if updates:
                try:
                    button = button.model_copy(update=updates)
                except Exception:
                    for key, value in updates.items():
                        try:
                            setattr(button, key, value)
                        except Exception:
                            pass
                changed = True
            new_row.append(button)
        new_rows.append(new_row)

    if not changed:
        return markup
    try:
        return markup.model_copy(update={"inline_keyboard": new_rows})
    except Exception:
        try:
            markup.inline_keyboard = new_rows
        except Exception:
            pass
        return markup
