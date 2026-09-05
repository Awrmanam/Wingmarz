from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardMarkup

import config
from operations_service import operations_service
from style_engine import style_engine
from trial_experience_service import trial_experience_service


service_marketplace_settings_router = Router(name="service_marketplace_settings")


def _sudo(user_id: int) -> bool:
    return int(user_id) in config.SUDO_ADMINS


async def _button(text: str, callback_data: str, *, icon_key: str | None = None, fallback: str | None = None):
    return await style_engine.styled_button(
        text,
        callback_data=callback_data,
        icon_key=icon_key,
        fallback=fallback,
    )


@service_marketplace_settings_router.callback_query(F.data == "ux:trialadmin:config")
async def service_config_trial_settings(callback: CallbackQuery):
    if not _sudo(callback.from_user.id):
        return
    settings = await operations_service.get_trial_settings()
    traffic_gb = settings["traffic_bytes"] / 1024**3
    minutes = settings["duration_seconds"] // 60
    cooldown = settings["cooldown_seconds"] / 3600
    await callback.message.edit_text(
        "🧪 <b>تست رایگان کانفیگ</b>\n\n"
        f"وضعیت: {'✅ فعال' if settings['enabled'] else '⛔ غیرفعال'}\n"
        f"حجم هر تست: <b>{traffic_gb:g} GB</b>\n"
        f"مدت هر تست: <b>{minutes} دقیقه</b>\n"
        f"فاصله دریافت مجدد: <b>{cooldown:g} ساعت</b>\n\n"
        "کاربر ابتدا «تست کانفیگ» را می‌زند و سپس سرویس Rebecca موردنظر (مثلاً WireGuard/OpenVPN) را انتخاب می‌کند.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [await _button("خاموش کردن" if settings["enabled"] else "فعال کردن", "ops:trial:toggle", fallback="🔁")],
            [
                await _button("تنظیم حجم", "ops:trial:traffic", fallback="📦"),
                await _button("تنظیم مدت", "ops:trial:duration", fallback="⏱"),
            ],
            [await _button("تنظیم فاصله دریافت", "ops:trial:cooldown", fallback="🕒")],
            [await _button("سرویس‌های قابل تست", "ux:trialadmin:plans", fallback="🔌")],
            [await _button("بازگشت", "cc:test", icon_key="back", fallback="⬅️")],
        ]),
    )
    await callback.answer()


@service_marketplace_settings_router.callback_query(F.data == "ux:trialadmin:panel")
async def service_panel_trial_settings(callback: CallbackQuery):
    if not _sudo(callback.from_user.id):
        return
    settings = await trial_experience_service.get_panel_trial_settings()
    await callback.message.edit_text(
        "🧩 <b>تست پنل نمایندگی</b>\n\n"
        f"وضعیت: {'✅ فعال' if settings['enabled'] else '⛔ غیرفعال'}\n"
        f"حجم کل پنل تست: <b>{settings['traffic_bytes'] / 1024**3:g} GB</b>\n"
        f"اعتبار: <b>{settings['duration_seconds'] / 3600:g} ساعت</b>\n"
        f"حداکثر کاربر: <b>{settings['max_users']}</b>\n"
        f"فاصله دریافت مجدد: <b>{settings['cooldown_seconds'] / 3600:g} ساعت</b>\n\n"
        "کاربر سرویس Rebecca را انتخاب می‌کند، نام کاربری دلخواه می‌دهد و رمز عبور توسط ربات ساخته می‌شود.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [await _button("خاموش کردن" if settings["enabled"] else "فعال کردن", "ux:paneltrial:toggle", fallback="🔁")],
            [
                await _button("حجم", "ux:paneltrial:set:traffic", fallback="📦"),
                await _button("اعتبار", "ux:paneltrial:set:duration", fallback="⏱"),
            ],
            [
                await _button("حد کاربر", "ux:paneltrial:set:users", fallback="👥"),
                await _button("فاصله دریافت", "ux:paneltrial:set:cooldown", fallback="🕒"),
            ],
            [await _button("سرویس‌های قابل تست", "ux:trialadmin:plans", fallback="🔌")],
            [await _button("بازگشت", "cc:test", icon_key="back", fallback="⬅️")],
        ]),
    )
    await callback.answer()
