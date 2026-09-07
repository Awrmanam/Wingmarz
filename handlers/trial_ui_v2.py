from __future__ import annotations

from html import escape
import math
import time
from typing import Any

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message, BufferedInputFile
from trial_delivery import connection_url, save_connections, load_connections

import config


# Register these defaults before PremiumUIService is first constructed. They then
# behave exactly like the older config.MESSAGES entries: persisted overrides are
# restored on startup and can be edited from the UI editor.
from message_catalog import TRIAL_UI_DEFAULTS

from database import db
from operations_service import OperationsError, operations_service
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
    if config.PANEL_PROVIDER != "rebecca":
        from handlers.trial_experience import _render_trial_plans
        await _render_trial_plans(message, trial_type)
        return
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
    if config.PANEL_PROVIDER != "rebecca":
        from handlers.trial_experience import public_config_trial_command
        await public_config_trial_command(message, state)
        return
    rows = await _trial_service_rows("config")
    rows.append([await _button("بازگشت", "trialv2:root", fallback="⬅️")])
    await message.answer(
        await _template("trial_v2_config_select"),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@trial_ui_v2_router.message(Command("paneltest"))
async def trial_panel_command(message: Message, state: FSMContext):
    await state.clear()
    if config.PANEL_PROVIDER != "rebecca":
        from handlers.trial_experience import public_panel_trial_command
        await public_panel_trial_command(message, state)
        return
    rows = await _trial_service_rows("panel")
    rows.append([await _button("بازگشت", "trialv2:root", fallback="⬅️")])
    await message.answer(
        await _template("trial_v2_panel_select"),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


def _catalog_id(data: str | None) -> int | None:
    try:
        return int(str(data or "").rsplit(":", 1)[-1])
    except (TypeError, ValueError):
        return None


@trial_ui_v2_router.callback_query(F.data.startswith("trialv2:cfg:") | F.data.startswith("svcmarket:trialcfg:"))
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
    await render_config_result(callback.message, callback.from_user.id, result, edit=True)
    await callback.answer("تست آماده شد")


async def render_config_result(message, user_id, result, *, edit=False):
    minutes = max(1, math.ceil((int(result["expire_at"]) - time.time()) / 60))
    body = await _template("trial_v2_config_success",
                           traffic=await format_traffic_size(int(result["traffic_bytes"])), minutes=minutes)
    rows = []
    url = connection_url(result.get("subscription_url"))
    if not url:
        url = next((connection_url(x) for x in result.get("links", []) if connection_url(x)), None)
    if url:
        rows.append([await _button("دریافت لینک اتصال", url=url, icon_key="link", fallback="🔗")])
    elif result.get("links"):
        token = await save_connections(user_id, result)
        rows.append([await _button("دریافت فایل اتصال", f"trialv2:links:{token}", icon_key="link", fallback="🔗")])
    else:
        body += "\n\nلینک اتصال دریافت نشد؛ لطفاً با پشتیبانی تماس بگیرید."
        rows.append([await _button("پشتیبانی", "support:home", fallback="🎧")])
    rows.append([await _button("بازگشت", "trialv2:root", icon_key="back", fallback="⬅️")])
    send = message.edit_text if edit else message.answer
    await send(body, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@trial_ui_v2_router.callback_query(F.data.startswith("trialv2:links:"))
async def trial_connection_file(callback):
    links = await load_connections((callback.data or "").rsplit(":", 1)[-1], callback.from_user.id)
    if not links:
        await callback.answer("لینک منقضی شده یا متعلق به شما نیست.", show_alert=True)
        return
    await callback.message.answer_document(BufferedInputFile("\n".join(links).encode(), filename="connection.txt"),
                                           caption="فایل اتصال تست شما")
    await callback.answer()


@trial_ui_v2_router.callback_query(F.data.startswith("trialv2:panel:") | F.data.startswith("svcmarket:trialpanel:"))
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
    if connection_url(login_url):
        rows.append([
            await _button("ورود به پنل", url=login_url, icon_key="panel", fallback="🌐")
        ])
    else:
        body += "\n\nبرای دریافت آدرس ورود با پشتیبانی تماس بگیرید."
    rows.append([await _button("بازگشت", "trialv2:root", icon_key="back", fallback="⬅️")])
    await message.answer(body, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
