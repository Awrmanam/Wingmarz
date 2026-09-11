from __future__ import annotations

import math
import time

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

import config
from panel_trial_delivery import load_active_panel_trial
from trial_delivery import connection_url
from handlers import trial_ui_v2 as trial


panel_trial_restore_router = Router(name="panel_trial_restore")


async def _render_active(message, result, *, edit: bool) -> None:
    remaining = max(0, int(result.get("expire_at") or 0) - int(time.time()))
    hours = max(1, math.ceil(remaining / 3600)) if remaining else 1
    body = await trial._template(
        "trial_v2_panel_success",
        username=result["username"],
        password=result["password"],
        hours=hours,
    )
    rows = []
    login_url = str(result.get("login_url") or "").strip()
    if connection_url(login_url):
        rows.append([
            await trial._button("ورود به پنل", url=login_url, icon_key="panel", fallback="🌐")
        ])
    else:
        body += "\n\nبرای دریافت آدرس ورود با پشتیبانی تماس بگیرید."
    rows.append([await trial._button("بازگشت", "trialv2:root", icon_key="back", fallback="⬅️")])
    send = message.edit_text if edit else message.answer
    await send(body, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


async def _active(user_id: int):
    if str(config.PANEL_PROVIDER or "").lower() != "rebecca":
        return None
    return await load_active_panel_trial(int(user_id))


@panel_trial_restore_router.callback_query(
    F.data.in_({"svcmarket:trial:panel", "ops:paneltrial:request", "trialv2:choose:panel"})
)
async def panel_entry(callback: CallbackQuery, state: FSMContext):
    current = await _active(callback.from_user.id)
    if current:
        await state.clear()
        await _render_active(callback.message, current, edit=True)
        await callback.answer("پنل تست فعال شما")
        return
    # Delegate the no-active path to the canonical existing implementation.
    await trial.trial_panel_picker(callback, state)


@panel_trial_restore_router.message(Command("paneltest"))
async def panel_command(message: Message, state: FSMContext):
    current = await _active(message.from_user.id)
    if current:
        await state.clear()
        await _render_active(message, current, edit=False)
        return
    await trial.trial_panel_command(message, state)


@panel_trial_restore_router.callback_query(
    F.data.startswith("trialv2:panel:") | F.data.startswith("svcmarket:trialpanel:")
)
async def panel_service(callback: CallbackQuery, state: FSMContext):
    # Idempotency guard for stale service-picker messages: an active panel is
    # always restored before the cooldown/issuance path is allowed to run.
    current = await _active(callback.from_user.id)
    if current:
        await state.clear()
        await _render_active(callback.message, current, edit=True)
        await callback.answer("پنل تست فعال شما")
        return
    await trial.panel_trial_selected(callback, state)
