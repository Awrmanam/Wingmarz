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


async def _customer_home(*, reseller: bool) -> InlineKeyboardMarkup:
    """Six main actions. Legacy buttons remain available in service/account hubs."""
    purchase = "admin_buy_reseller" if reseller else "public_buy_reseller"
    return InlineKeyboardMarkup(inline_keyboard=[
        [await _button("خرید سرویس", purchase, icon_key="buy", fallback="🛒"),
         await _button("سرویس‌های من", "wallet:accounts", icon_key="plan", fallback="📦")],
        [await _button("کیف پول من", "wallet:home", icon_key="wallet", fallback="💰"),
         await _button("گزارش‌های من", "customer:reports", icon_key="report", fallback="📊")],
        [await _button("تعرفه و تست", "customer:explore", icon_key="price", fallback="🧪"),
         await _button("حساب و پشتیبانی", "customer:account", icon_key="support", fallback="🎧")],
    ])


async def public_home_keyboard() -> InlineKeyboardMarkup:
    """Compact public/customer home, backed by the same account center."""
    return await _customer_home(reseller=False)


async def reseller_home_keyboard() -> InlineKeyboardMarkup:
    """Compact reseller home; specific panel actions live under My Services."""
    return await _customer_home(reseller=True)


async def customer_hub_keyboard(kind: str, *, reseller: bool) -> InlineKeyboardMarkup:
    """Secondary actions retain the existing business callbacks unchanged."""
    if kind == "reports":
        rows=[
            [await _button("گزارش مصرف PAYG", "wallet:reports", icon_key="report", fallback="📊"),
             await _button("گردش مالی", "wallet:ledger:all:0", icon_key="receipt", fallback="🧾")],
        ]
        if reseller:
            rows.append([await _button(config.BUTTONS["my_report"], "my_report",
                                       icon_key="report", fallback="📈")])
    elif kind == "explore":
        rows=[
            [await _button("تعرفه‌ها", "tariffs:home", icon_key="price", fallback="💰"),
             await _button("تست رایگان", "svcmarket:trial", icon_key="test", fallback="🧪")],
        ]
    elif kind == "account":
        rows=[
            [await _button("وضعیت احراز هویت", "kyc:status", icon_key="kyc", fallback="🪪"),
             await _button("پشتیبانی", "support:home", icon_key="support", fallback="🎧")],
        ]
        if reseller:
            rows.extend([
                [await _button(config.BUTTONS["my_info"], "my_info", icon_key="info", fallback="👤"),
                 await _button(config.BUTTONS["my_users"], "my_users", icon_key="users", fallback="👥")],
                [await _button(config.BUTTONS["reactivate_users"], "reactivate_users",
                               icon_key="refresh", fallback="🔄")],
            ])
    else:
        raise ValueError("unknown customer hub")
    rows.append([await _button("بازگشت به صفحه اصلی", "back_to_admin_main", icon_key="back", fallback="🔙")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
