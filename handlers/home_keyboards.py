from __future__ import annotations

import re

from aiogram.types import InlineKeyboardMarkup

import config
from style_engine import style_engine


def _clean(text: str) -> str:
    """Remove legacy leading Unicode decoration; Premium UI supplies the icon."""
    value = str(text or "").strip()
    return re.sub(r"^[^\w@]+", "", value, flags=re.UNICODE).strip() or value


async def _button(
    text: str,
    callback_data: str,
    *,
    icon_key: str | None = None,
    fallback: str | None = None,
):
    return await style_engine.styled_button(
        _clean(text),
        callback_data=callback_data,
        icon_key=icon_key,
        fallback=fallback,
    )


async def public_home_keyboard() -> InlineKeyboardMarkup:
    """Canonical editable keyboard for public/customer home."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [await _button("خرید پنل نمایندگی", "public_buy_reseller", icon_key="buy", fallback="🛒")],
        [await _button("تست رایگان", "svcmarket:trial", icon_key="test", fallback="🧪")],
        [await _button("پشتیبانی", "support:home", icon_key="support", fallback="🎧")],
    ])


async def reseller_home_keyboard() -> InlineKeyboardMarkup:
    """Canonical editable keyboard for reseller home.

    The callbacks are exactly the legacy business callbacks; only presentation is
    centralized through StyleEngine/PremiumUIService.
    """
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            await _button(config.BUTTONS["my_info"], "my_info", icon_key="info", fallback="👤"),
            await _button(config.BUTTONS["my_report"], "my_report", icon_key="report", fallback="📈"),
        ],
        [
            await _button(config.BUTTONS["my_users"], "my_users", icon_key="users", fallback="👥"),
            await _button(config.BUTTONS["reactivate_users"], "reactivate_users", icon_key="refresh", fallback="🔄"),
        ],
        [await _button("خرید پنل نمایندگی", "admin_buy_reseller", icon_key="buy", fallback="🛒")],
        [await _button(config.BUTTONS["renew"], "admin_renew", icon_key="renew", fallback="🔄")],
        [await _button("تست رایگان", "svcmarket:trial", icon_key="test", fallback="🧪")],
        [await _button("پشتیبانی", "support:home", icon_key="support", fallback="🎧")],
    ])
