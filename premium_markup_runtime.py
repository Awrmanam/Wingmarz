from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup

from premium_ui_service import premium_ui_service
from style_engine import style_engine


async def style_reply_markup(markup):
    """Catalog and style callback buttons from any handler, including legacy ones.

    The callback_data itself is never changed. On aiogram versions without
    Telegram's custom button-icon field, the selected Premium Emoji's Unicode
    fallback is prefixed to the visible button text.
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
            override = premium_ui_service._button_overrides.get(callback_data)
            if not override:
                new_row.append(button)
                continue

            display_text, emoji_key = override
            text = display_text or original_text
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
