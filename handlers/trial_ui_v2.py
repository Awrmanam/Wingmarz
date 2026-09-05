from __future__ import annotations

from html import escape
import math
import time
from typing import Any

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

import config


# Register these defaults before PremiumUIService is first constructed. They then
# behave exactly like the older config.MESSAGES entries: persisted overrides are
# restored on startup and can be edited from the UI editor.
TRIAL_UI_DEFAULTS: dict[str, str] = {
    "trial_v2_root": (
        "🧪 <b>تست رایگان</b>\n\n"
        "نوع تستی که می‌خواهید دریافت کنید را انتخاب کنید."
    ),
    "trial_v2_config_select": (
        "🧪 <b>تست رایگان کانفیگ</b>\n\n"
        "نوع سرویس موردنظر را انتخاب کنید:"
    ),
    "trial_v2_panel_select": (
        "🧩 <b>تست پنل نمایندگی</b>\n\n"
        "نوع سرویس موردنظر را انتخاب کنید:"
    ),
    "trial_v2_config_success": (
        "✅ <b>کانفیگ تست شما آماده شد</b>\n\n"
        "📦 حجم: <b>{traffic}</b>\n"
        "⏱ اعتبار: <b>{minutes} دقیقه</b>\n\n"
        "برای اتصال از دکمه زیر استفاده کنید."
    ),
    "trial_v2_panel_username": (
        "🧩 <b>دریافت پنل تست</b>\n\n"
        "نام کاربری دلخواهتان را ارسال کنید.\n"
        "رمز عبور به‌صورت امن توسط ربات ساخته می‌شود.\n\n"
        "مثال: <code>arman_test</code>"
    ),
    "trial_v2_panel_success": (
        "✅ <b>پنل تست شما آماده شد</b>\n\n"
        "👤 نام کاربری: <code>{username}</code>\n"
        "🔑 رمز عبور: <code>{password}</code>\n"
        "⏱ اعتبار: <b>{hours} ساعت</b>\n\n"
        "برای ورود از دکمه زیر استفاده کنید."
    ),
}
for _key, _body in TRIAL_UI_DEFAULTS.items():
    config.MESSAGES.setdefault(_key, _body)

from database import db
from operations_service import OperationsError
from premium_ui_service import premium_ui_service
from service_marketplace_service import service_marketplace_service
from style_engine import style_engine
from trial_experience_service import trial_experience_service
from utils.notify import format_traffic_size


trial_ui_v2_router = Router(name="trial_ui_v2")


class TrialPanelV2States(StatesGroup):
    username = State()


async def _button(
    text: str,
    callback_data: str | None = None,
    *,
    url: str | None = None,
    icon_key: str | None = None,
    fallback: str | None = None,
):
    kwargs: dict[str, Any] = {}
    if callback_data is not None:
        kwargs["callback_data"] = callback_data
    if url is not None:
        kwargs["url"] = url
    return await style_engine.styled_button(
        text,
        icon_key=icon_key,
        fallback=fallback,
        **kwargs,
    )


async def _template(key: str, **values: Any) -> str:
    body = str(config.MESSAGES.get(key, TRIAL_UI_DEFAULTS.get(key, "")))
    # Deliberately use literal replacement instead of str.format so
    # {emoji:key} placeholders survive until PremiumUIService renders them.
    for name, value in values.items():
        body = body.replace("{" + str(name) + "}", escape(str(value)))
    return await premium_ui_service.render_placeholders(body)


async def _home_callback(user_id: int) -> str:
    return "back_to_admin_main" if await db.is_admin_authorized(int(user_id)) else "public_back_main"


async def _trial_service_rows(trial_type: str) -> list[list[Any]]:
    services = await service_marketplace_service.trial_services(trial_type)
    rows: list[list[Any]] = []
    for service in services:
        callback_data = (
            f"trialv2:cfg:{int(service.id)}"
            if trial_type == "config"
            else f"trialv2:panel:{int(service.id)}"
        )
        rows.append([
            await _button(
                str(service.display_name),
                callback_data,
                icon_key="rebecca",
                fallback="🔌",
            )
        ])
    return rows


async def _render_root(message: Message, user_id: int) -> None:
    rows = [
        [await _button("کانفیگ تست", "trialv2:choose:config", icon_key="test", fallback="🧪")],
        [await _button("پنل نمایندگی تست", "trialv2:choose:panel", icon_key="panel", fallback="🧩")],
        [await _button("بازگشت", await _home_callback(user_id), icon_key="back", fallback="⬅️")],
    ]
    await message.edit_text(
        await _template("trial_v2_root"),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


async def _render_service_picker(message: Message, user_id: int, trial_type: str) -> None:
    rows = await _trial_service_rows(trial_type)
    if not rows:
        rows.append([
            await _button("بازگشت", "trialv2:root", icon_key="back", fallback="⬅️")
        ])
        await message.edit_text(
            "⛔ در حال حاضر سرویسی برای این نوع تست فعال نیست.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )
        return
    rows.append([await _button("بازگشت", "trialv2:root", icon_key="back", fallback="⬅️")])
    key = "trial_v2_config_select" if trial_type == "config" else "trial_v2_panel_select"
    await message.edit_text(
        await _template(key),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@trial_ui_v2_router.callback_query(F.data.in_({"svcmarket:trial", "trialv2:root"}))
async def trial_root(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await _render_root(callback.message, callback.from_user.id)
    await callback.answer()


@trial_ui_v2_router.callback_query(
    F.data.in_({"svcmarket:trial:config", "ops:configtrial:request", "ops:trial:request", "trialv2:choose:config"})
)
async def trial_config_picker(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await _render_service_picker(callback.message, callback.from_user.id, "config")
    await callback.answer()


@trial_ui_v2_router.callback_query(
    F.data.in_({"svcmarket:trial:panel", "ops:paneltrial:request", "trialv2:choose:panel"})
)
async def trial_panel_picker(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await _render_service_picker(callback.message, callback.from_user.id, "panel")
    await callback.answer()


@trial_ui_v2_router.message(Command("test"))
async def trial_config_command(message: Message, state: FSMContext):
    await state.clear()
    rows = await _trial_service_rows("config")
    await message.answer(
        await _template("trial_v2_config_select"),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@trial_ui_v2_router.message(Command("paneltest"))
async def trial_panel_command(message: Message, state: FSMContext):
    await state.clear()
    rows = await _trial_service_rows("panel")
    await message.answer(
        await _template("trial_v2_panel_select"),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


def _catalog_id(data: str | None) -> int | None:
    try:
        return int(str(data or "").rsplit(":", 1)[-1])
    except (TypeError, ValueError):
        return None


@trial_ui_v2_router.callback_query(F.data.startswith("trialv2:cfg:"))
async def issue_config_trial(callback: CallbackQuery, state: FSMContext):
    catalog_id = _catalog_id(callback.data)
    if not catalog_id:
        await callback.answer("نامعتبر", show_alert=True)
        return
    service = await service_marketplace_service.get_service_by_catalog_id(catalog_id)
    if not service:
        await callback.answer("این سرویس دیگر فعال نیست.", show_alert=True)
        return
    try:
        result = await service_marketplace_service.issue_config_trial_for_service(
            callback.from_user.id,
            service.rebecca_service_id,
        )
    except OperationsError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await state.clear()
    remaining = max(0, int(result["expire_at"]) - int(time.time()))
    minutes = max(1, math.ceil(remaining / 60))
    traffic = await format_traffic_size(int(result["traffic_bytes"]))
    body = await _template(
        "trial_v2_config_success",
        traffic=traffic,
        minutes=minutes,
    )

    rows: list[list[Any]] = []
    subscription_url = str(result.get("subscription_url") or "").strip()
    if subscription_url.startswith(("https://", "http://")):
        rows.append([
            await _button(
                "دریافت لینک اشتراک",
                url=subscription_url,
                icon_key="link",
                fallback="🔗",
            )
        ])
    else:
        # If Rebecca did not return an HTTP subscription URL, keep the result
        # usable without flooding the normal success card with every raw link.
        extra_links = [str(item).strip() for item in result.get("links", []) if str(item).strip()]
        if extra_links:
            body += "\n\n🔗 <b>لینک اتصال:</b>"
            for idx, link in enumerate(extra_links[:3], start=1):
                body += f'\n<a href="{escape(link)}">لینک {idx}</a>'

    rows.append([await _button("بازگشت", "trialv2:root", icon_key="back", fallback="⬅️")])
    await callback.message.edit_text(
        body,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer("تست آماده شد")


@trial_ui_v2_router.callback_query(F.data.startswith("trialv2:panel:"))
async def panel_trial_selected(callback: CallbackQuery, state: FSMContext):
    catalog_id = _catalog_id(callback.data)
    if not catalog_id:
        await callback.answer("نامعتبر", show_alert=True)
        return
    service = await service_marketplace_service.get_service_by_catalog_id(catalog_id)
    if not service:
        await callback.answer("این سرویس دیگر فعال نیست.", show_alert=True)
        return
    settings = await trial_experience_service.get_panel_trial_settings()
    if not settings["enabled"]:
        await callback.answer("تست پنل فعلاً غیرفعال است.", show_alert=True)
        return
    wait = await trial_experience_service.panel_trial_wait_seconds(callback.from_user.id)
    if wait > 0:
        await callback.answer(
            f"برای دریافت تست بعدی حدود {max(1, math.ceil(wait / 3600))} ساعت صبر کنید.",
            show_alert=True,
        )
        return

    await state.clear()
    await state.update_data(trial_v2_catalog_id=catalog_id)
    await state.set_state(TrialPanelV2States.username)
    await callback.message.edit_text(
        await _template("trial_v2_panel_username"),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            await _button("بازگشت", "trialv2:choose:panel", icon_key="back", fallback="⬅️")
        ]]),
    )
    await callback.answer()


@trial_ui_v2_router.message(TrialPanelV2States.username, F.text)
async def issue_panel_trial(message: Message, state: FSMContext):
    data = await state.get_data()
    catalog_id = int(data.get("trial_v2_catalog_id") or 0)
    service = await service_marketplace_service.get_service_by_catalog_id(catalog_id)
    if not service:
        await state.clear()
        await message.answer("❌ این سرویس دیگر فعال نیست.")
        return
    try:
        result = await service_marketplace_service.issue_panel_trial_for_service(
            user_id=message.from_user.id,
            service_id=service.rebecca_service_id,
            requested_username=message.text or "",
            telegram_username=message.from_user.username,
            first_name=message.from_user.first_name,
            last_name=message.from_user.last_name,
        )
    except OperationsError as exc:
        await message.answer(f"❌ {escape(str(exc))}")
        return

    await state.clear()
    remaining = max(0, int(result.get("expire_at") or 0) - int(time.time()))
    hours = max(1, math.ceil(remaining / 3600)) if remaining else 1
    body = await _template(
        "trial_v2_panel_success",
        username=result["username"],
        password=result["password"],
        hours=hours,
    )
    rows: list[list[Any]] = []
    login_url = str(result.get("login_url") or "").strip()
    if login_url.startswith(("https://", "http://")):
        rows.append([
            await _button("ورود به پنل", url=login_url, icon_key="panel", fallback="🌐")
        ])
    elif login_url:
        body += f"\n\n🌐 آدرس ورود:\n<code>{escape(login_url)}</code>"
    rows.append([await _button("بازگشت", "trialv2:root", icon_key="back", fallback="⬅️")])
    await message.answer(body, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
