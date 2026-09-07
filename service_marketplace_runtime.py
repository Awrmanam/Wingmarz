from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from handlers import admin_handlers, public_handlers


def _with_trial_button(markup: InlineKeyboardMarkup) -> InlineKeyboardMarkup:
    rows = [list(row) for row in markup.inline_keyboard]
    if any(
        getattr(button, "callback_data", None) == "svcmarket:trial"
        for row in rows
        for button in row
    ):
        return markup
    insert_at = 1 if rows else 0
    rows.insert(
        insert_at,
        [InlineKeyboardButton(text="🧪 تست رایگان", callback_data="svcmarket:trial")],
    )
    return markup.model_copy(update={"inline_keyboard": rows})


def service_market_admin_keyboard():
    return admin_handlers.get_admin_keyboard()


def service_market_public_keyboard():
    return public_handlers.get_public_main_keyboard()
