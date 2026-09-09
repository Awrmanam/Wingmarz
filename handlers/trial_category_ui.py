from __future__ import annotations

from typing import Any

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

import config
from product_catalog import product_catalog
from service_marketplace_service import service_marketplace_service
from style_engine import style_engine


trial_category_router = Router(name="trial_category_ui")


async def _button(text: str, callback_data: str, *, fallback: str | None = None, icon_key: str | None = None):
    return await style_engine.styled_button(
        text,
        callback_data=callback_data,
        fallback=fallback,
        icon_key=icon_key,
    )


async def _rows(trial_type: str) -> list[list[Any]]:
    services = await service_marketplace_service.trial_services(trial_type)
    rows: list[list[Any]] = []
    for service in services:
        category = await product_catalog.get_category_by_service(service.rebecca_service_id)
        if not category or not category.is_active or not category.provider_enabled:
            continue
        callback_data = (
            f"trialv2:cfg:{int(service.id)}"
            if trial_type == "config"
            else f"trialv2:panel:{int(service.id)}"
        )
        icon_key = await product_catalog.resolve_panel_icon_key(category)
        rows.append([
            await _button(
                category.name,
                callback_data,
                icon_key=icon_key,
                fallback="📁" if not icon_key else None,
            )
        ])
    return rows


async def _render(message: Message, trial_type: str, *, answer: bool = False) -> None:
    rows = await _rows(trial_type)
    if not rows:
        rows.append([await _button("بازگشت", "trialv2:root", icon_key="back", fallback="⬅️")])
        text = "⛔ در حال حاضر گزینه‌ای برای این نوع تست فعال نیست."
    else:
        rows.append([await _button("بازگشت", "trialv2:root", icon_key="back", fallback="⬅️")])
        text = (
            "🧪 <b>تست رایگان کانفیگ</b>\n\nپنل موردنظر را انتخاب کنید:"
            if trial_type == "config"
            else "🧩 <b>تست پنل نمایندگی</b>\n\nپنل موردنظر را انتخاب کنید:"
        )
    markup = InlineKeyboardMarkup(inline_keyboard=rows)
    if answer:
        await message.answer(text, reply_markup=markup)
    else:
        await message.edit_text(text, reply_markup=markup)


@trial_category_router.callback_query(
    F.data.in_({"svcmarket:trial:config", "ops:configtrial:request", "ops:trial:request", "trialv2:choose:config"})
)
async def trial_category_config(callback: CallbackQuery, state: FSMContext):
    if config.PANEL_PROVIDER != "rebecca":
        return
    await state.clear()
    await _render(callback.message, "config")
    await callback.answer()


@trial_category_router.callback_query(
    F.data.in_({"svcmarket:trial:panel", "ops:paneltrial:request", "trialv2:choose:panel"})
)
async def trial_category_panel(callback: CallbackQuery, state: FSMContext):
    if config.PANEL_PROVIDER != "rebecca":
        return
    await state.clear()
    await _render(callback.message, "panel")
    await callback.answer()


@trial_category_router.message(Command("test"))
async def trial_category_test_command(message: Message, state: FSMContext):
    if config.PANEL_PROVIDER != "rebecca":
        return
    await state.clear()
    await _render(message, "config", answer=True)


@trial_category_router.message(Command("paneltest"))
async def trial_category_panel_command(message: Message, state: FSMContext):
    if config.PANEL_PROVIDER != "rebecca":
        return
    await state.clear()
    await _render(message, "panel", answer=True)
