from __future__ import annotations

from typing import Any

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

import config
from database import db
from rebecca_catalog import list_services
from service_marketplace_service import service_marketplace_service
from style_engine import style_engine


plan_storefront_router = Router(name="plan_storefront")


async def _button(
    text: str,
    callback_data: str,
    *,
    icon_key: str | None = None,
    fallback: str | None = None,
):
    return await style_engine.styled_button(
        text,
        callback_data=callback_data,
        icon_key=icon_key,
        fallback=fallback,
    )


async def _sellable_plans() -> list[Any]:
    """Return active paid plans mapped to at least one active Rebecca service.

    Rebecca services are provider-side routing/inbound metadata. Paid customers
    choose commercial plans; the provider mapping stays internal.
    """
    active_services = await list_services(enabled_only=True)
    active_ids = {int(item.rebecca_service_id) for item in active_services}
    if not active_ids:
        return []

    plans = await db.get_plans(only_active=True)
    result = [
        plan
        for plan in plans
        if active_ids.intersection(service_marketplace_service.plan_service_ids(plan))
    ]
    return sorted(
        result,
        key=lambda plan: (
            getattr(plan, "time_limit_seconds", None) is None,
            int(getattr(plan, "time_limit_seconds", 0) or 0),
            int(getattr(plan, "price", 0) or 0),
            int(getattr(plan, "id", 0) or 0),
        ),
    )


async def _plan_label(plan: Any) -> str:
    name = str(getattr(plan, "name", "پلن")).strip() or "پلن"
    price = int(getattr(plan, "price", 0) or 0)
    return f"{name} · {price:,} ت"


def _home_callback(source: str) -> str:
    return "back_to_admin_main" if source == "a" else "public_back_main"


async def _render_plan_rows(
    message: Message,
    *,
    source: str,
    plans: list[Any],
    duration_label: str | None = None,
) -> None:
    rows = []
    for plan in plans:
        callback_data = (
            f"admin_order_{int(plan.id)}"
            if source == "a"
            else f"public_order_{int(plan.id)}"
        )
        rows.append([
            await _button(
                await _plan_label(plan),
                callback_data,
                icon_key="plan",
                fallback="📦",
            )
        ])

    back = f"planmarket:root:{source}" if duration_label else _home_callback(source)
    rows.append([await _button("بازگشت", back, icon_key="back", fallback="⬅️")])

    title = str(
        config.MESSAGES.get(
            "sales_plan_select_public",
            "🛒 <b>پلن‌های نمایندگی</b>\n\nپلن موردنظر را انتخاب کنید:",
        )
    )
    if duration_label:
        title += f"\n\n🗓 <b>{duration_label}</b>"
    await message.edit_text(title, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


async def _render_storefront(message: Message, source: str) -> None:
    plans = await _sellable_plans()
    if not plans:
        await message.edit_text(
            str(config.MESSAGES.get("sales_empty", "فعلاً پلنی برای خرید آماده نیست.")),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                await _button("بازگشت", _home_callback(source), icon_key="back", fallback="⬅️")
            ]]),
        )
        return

    groups = service_marketplace_service.group_plans_by_duration(plans)
    if await service_marketplace_service.duration_groups_enabled() and len(groups) > 1:
        rows = [
            [
                await _button(
                    group.label,
                    f"planmarket:d:{source}:{group.key}",
                    fallback="🗓",
                )
            ]
            for group in groups
        ]
        rows.append([
            await _button("بازگشت", _home_callback(source), icon_key="back", fallback="⬅️")
        ])
        await message.edit_text(
            str(
                config.MESSAGES.get(
                    "sales_duration_select_public",
                    "🛒 <b>پلن‌های نمایندگی</b>\n\nمدت موردنظر را انتخاب کنید:",
                )
            ),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )
        return

    await _render_plan_rows(message, source=source, plans=plans)


@plan_storefront_router.callback_query(F.data == "admin_buy_reseller")
async def plan_storefront_admin(callback: CallbackQuery, state: FSMContext):
    if config.PANEL_PROVIDER != "rebecca":
        return
    await state.clear()
    await _render_storefront(callback.message, "a")
    await callback.answer()


@plan_storefront_router.callback_query(F.data == "public_buy_reseller")
async def plan_storefront_public(callback: CallbackQuery, state: FSMContext):
    if config.PANEL_PROVIDER != "rebecca":
        return
    await state.clear()
    await _render_storefront(callback.message, "p")
    await callback.answer()


@plan_storefront_router.callback_query(F.data.startswith("planmarket:root:"))
@plan_storefront_router.callback_query(F.data.startswith("svcmarket:root:"))
async def plan_storefront_root(callback: CallbackQuery, state: FSMContext):
    source = (callback.data or "").rsplit(":", 1)[-1]
    if source not in {"a", "p"} or config.PANEL_PROVIDER != "rebecca":
        return
    await state.clear()
    await _render_storefront(callback.message, source)
    await callback.answer()


@plan_storefront_router.callback_query(F.data.startswith("planmarket:d:"))
async def plan_storefront_duration(callback: CallbackQuery, state: FSMContext):
    parts = (callback.data or "").split(":")
    if len(parts) != 4 or parts[2] not in {"a", "p"}:
        await callback.answer("نامعتبر", show_alert=True)
        return
    source, key = parts[2], parts[3]
    plans = await _sellable_plans()
    groups = {
        item.key: item
        for item in service_marketplace_service.group_plans_by_duration(plans)
    }
    group = groups.get(key)
    if not group:
        await callback.answer("این دسته زمانی دیگر موجود نیست.", show_alert=True)
        return
    await state.clear()
    await _render_plan_rows(
        callback.message,
        source=source,
        plans=list(group.plans),
        duration_label=group.label,
    )
    await callback.answer()
