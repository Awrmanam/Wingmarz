from __future__ import annotations

from html import escape
import math
from typing import Any

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

import config
from product_catalog import product_catalog
from rebecca_catalog import get_service
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


def _home_callback(source: str) -> str:
    return "back_to_admin_main" if source == "a" else "public_back_main"


def _plan_callback(source: str, plan_id: int) -> str:
    return f"admin_order_{int(plan_id)}" if source == "a" else f"public_order_{int(plan_id)}"


def _traffic_text(value: int | None) -> str:
    if not value:
        return "نامحدود"
    return f"{float(value) / (1024 ** 3):g} GB"


def _duration_text(value: int | None) -> str:
    if not value:
        return "نامحدود"
    return service_marketplace_service.duration_label(int(value))


def _users_text(value: int | None) -> str:
    return "نامحدود" if value is None else str(int(value))


async def _categories() -> list[Any]:
    return await product_catalog.categories(active_only=True, sellable_only=True)


async def _render_storefront(message: Message, source: str) -> None:
    categories = await _categories()
    if not categories:
        await message.edit_text(
            str(config.MESSAGES.get("sales_empty", "فعلاً پلنی برای خرید آماده نیست.")),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                await _button("بازگشت", _home_callback(source), icon_key="back", fallback="⬅️")
            ]]),
        )
        return

    rows = []
    for category in categories:
        plans = await product_catalog.plans_for_category(category.id, only_active=True)
        rows.append([
            await _button(
                f"{category.name} · {len(plans)} پلن",
                f"planmarket:c:{source}:{category.id}",
                fallback="📁",
            )
        ])
    rows.append([await _button("بازگشت", _home_callback(source), icon_key="back", fallback="⬅️")])
    await message.edit_text(
        "🛒 <b>خرید پنل نمایندگی</b>\n\nنوع پنل را انتخاب کنید:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


async def _render_category(message: Message, source: str, category_id: int) -> None:
    category = await product_catalog.get_category(category_id)
    if not category or not category.is_active:
        await message.edit_text("این دسته در حال حاضر فعال نیست.")
        return
    plans = await product_catalog.plans_for_category(category_id, only_active=True)
    if not plans:
        await message.edit_text(
            "فعلاً پلن فعالی در این دسته وجود ندارد.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                await _button("بازگشت", f"planmarket:root:{source}", fallback="⬅️")
            ]]),
        )
        return

    grouping = await service_marketplace_service.duration_groups_enabled()
    groups = service_marketplace_service.group_plans_by_duration(plans)
    if grouping and len(groups) > 1:
        rows = [
            [await _button(group.label, f"planmarket:d:{source}:{category_id}:{group.key}", fallback="🗓")]
            for group in groups
        ]
        rows.append([await _button("بازگشت", f"planmarket:root:{source}", icon_key="back", fallback="⬅️")])
        body = f"📁 <b>{escape(category.name)}</b>\n"
        if category.description:
            body += f"\n{escape(category.description)}\n"
        body += "\nمدت موردنظر را انتخاب کنید:"
        await message.edit_text(body, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
        return

    await _render_plan_list(message, source, category_id, plans)


async def _render_plan_list(
    message: Message,
    source: str,
    category_id: int,
    plans: list[Any],
    *,
    duration_key: str | None = None,
    duration_label: str | None = None,
) -> None:
    category = await product_catalog.get_category(category_id)
    if not category:
        await message.edit_text("این دسته دیگر موجود نیست.")
        return
    rows = []
    for plan in plans:
        rows.append([
            await _button(
                f"{plan.name} · {int(plan.price or 0):,} ت",
                f"planmarket:p:{source}:{category_id}:{int(plan.id)}:{duration_key or '-'}",
                icon_key="plan",
                fallback="📦",
            )
        ])
    back = f"planmarket:c:{source}:{category_id}" if duration_key else f"planmarket:root:{source}"
    rows.append([await _button("بازگشت", back, icon_key="back", fallback="⬅️")])
    body = f"📦 <b>پلن‌های {escape(category.name)}</b>"
    if duration_label:
        body += f"\n🗓 {escape(duration_label)}"
    body += "\n\nپلن موردنظر را انتخاب کنید:"
    await message.edit_text(body, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


async def _render_plan_detail(
    message: Message,
    source: str,
    category_id: int,
    plan_id: int,
    duration_key: str | None,
) -> None:
    category = await product_catalog.get_category(category_id)
    from database import db
    plan = await db.get_plan_by_id(plan_id)
    if not category or not category.is_active or not plan or not plan.is_active:
        await message.edit_text("این پلن دیگر برای فروش فعال نیست.")
        return
    if category.rebecca_service_id not in service_marketplace_service.plan_service_ids(plan):
        await message.edit_text("این پلن دیگر در این دسته قرار ندارد.")
        return
    description = await product_catalog.plan_description(plan_id)
    body = (
        f"📦 <b>{escape(plan.name)}</b>\n"
        f"📁 {escape(category.name)}\n\n"
    )
    if description:
        body += f"{escape(description)}\n\n"
    body += (
        f"📊 حجم: <b>{_traffic_text(plan.traffic_limit_bytes)}</b>\n"
        f"🗓 مدت: <b>{_duration_text(plan.time_limit_seconds)}</b>\n"
        f"👥 کاربران: <b>{_users_text(plan.max_users)}</b>\n"
        f"💵 قیمت: <b>{int(plan.price or 0):,} تومان</b>"
    )
    if duration_key and duration_key != "-":
        back = f"planmarket:d:{source}:{category_id}:{duration_key}"
    else:
        back = f"planmarket:c:{source}:{category_id}"
    rows = [
        [await _button("✅ انتخاب این پلن", _plan_callback(source, plan_id), fallback="✅")],
        [await _button("بازگشت", back, icon_key="back", fallback="⬅️")],
    ]
    await message.edit_text(body, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


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


@plan_storefront_router.callback_query(F.data.startswith("planmarket:c:"))
async def plan_storefront_category(callback: CallbackQuery, state: FSMContext):
    parts = (callback.data or "").split(":")
    if len(parts) != 4 or parts[2] not in {"a", "p"}:
        await callback.answer("نامعتبر", show_alert=True)
        return
    await state.clear()
    await _render_category(callback.message, parts[2], int(parts[3]))
    await callback.answer()


@plan_storefront_router.callback_query(F.data.startswith("planmarket:d:"))
async def plan_storefront_duration(callback: CallbackQuery, state: FSMContext):
    parts = (callback.data or "").split(":")
    if len(parts) != 5 or parts[2] not in {"a", "p"}:
        await callback.answer("نامعتبر", show_alert=True)
        return
    source, category_id, key = parts[2], int(parts[3]), parts[4]
    plans = await product_catalog.plans_for_category(category_id, only_active=True)
    groups = {item.key: item for item in service_marketplace_service.group_plans_by_duration(plans)}
    group = groups.get(key)
    if not group:
        await callback.answer("این دسته زمانی دیگر موجود نیست.", show_alert=True)
        return
    await state.clear()
    await _render_plan_list(
        callback.message,
        source,
        category_id,
        list(group.plans),
        duration_key=group.key,
        duration_label=group.label,
    )
    await callback.answer()


@plan_storefront_router.callback_query(F.data.startswith("planmarket:p:"))
async def plan_storefront_plan(callback: CallbackQuery, state: FSMContext):
    parts = (callback.data or "").split(":")
    if len(parts) != 6 or parts[2] not in {"a", "p"}:
        await callback.answer("نامعتبر", show_alert=True)
        return
    source, category_id, plan_id = parts[2], int(parts[3]), int(parts[4])
    duration_key = None if parts[5] == "-" else parts[5]
    await state.clear()
    await _render_plan_detail(callback.message, source, category_id, plan_id, duration_key)
    await callback.answer()


@plan_storefront_router.callback_query(F.data.startswith("svcmarket:s:"))
async def old_service_button_compat(callback: CallbackQuery, state: FSMContext):
    """Old already-sent service buttons are translated to the new public category."""
    parts = (callback.data or "").split(":")
    if len(parts) != 4 or parts[2] not in {"a", "p"} or config.PANEL_PROVIDER != "rebecca":
        return
    try:
        old_service = await get_service(int(parts[3]))
    except ValueError:
        old_service = None
    if not old_service:
        await callback.answer("این گزینه قدیمی شده؛ دوباره وارد خرید شوید.", show_alert=True)
        return
    category = await product_catalog.get_category_by_service(old_service.rebecca_service_id)
    if not category or not category.is_active:
        await callback.answer("این دسته در حال حاضر فعال نیست.", show_alert=True)
        return
    await state.clear()
    await _render_category(callback.message, parts[2], category.id)
    await callback.answer()
