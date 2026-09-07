from __future__ import annotations

from html import escape
import math
from typing import Any

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

import config
from authorization import is_staff
from database import db
from operations_service import OperationsError, operations_service
from premium_ui_service import premium_ui_service
from service_marketplace_service import service_marketplace_service
from style_engine import style_engine
from trial_experience_service import trial_experience_service
from utils.notify import format_traffic_size, seconds_to_days
from utils.rebecca import credential_message


service_marketplace_router = Router(name="service_marketplace")


class ServicePanelTrialStates(StatesGroup):
    username = State()


def _is_sudo(user_id: int) -> bool:
    return is_staff(user_id)


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


async def _render_tokens(value: str) -> str:
    return await premium_ui_service.render_placeholders(str(value))


async def _service_label(service: Any) -> str:
    return str(service.display_name)


async def _plan_button_label(plan: Any) -> str:
    name = str(plan.name)
    price = int(getattr(plan, "price", 0) or 0)
    return f"{name} · {price:,} ت"


async def _home_callback_for(user_id: int) -> str:
    return "back_to_admin_main" if await db.is_admin_authorized(int(user_id)) else "public_back_main"


# ---------------------------------------------------------------------------
# Service-first paid purchase
# ---------------------------------------------------------------------------

async def _render_purchase_services(message: Message, source: str) -> None:
    services = await service_marketplace_service.sellable_services()
    if len(services) == 1:
        service = services[0][0]
        plans = await service_marketplace_service.plans_for_service(service.rebecca_service_id)
        groups = service_marketplace_service.group_plans_by_duration(plans)
        home = "back_to_admin_main" if source == "a" else "public_back_main"
        if await service_marketplace_service.duration_groups_enabled() and len(groups) > 1:
            rows = [[await _button(group.label, f"svcmarket:d:{source}:{int(service.id)}:{group.key}")] for group in groups]
            rows.append([await _button("بازگشت", home, fallback="⬅️")])
            title = await _render_tokens(escape(str(service.display_name)))
            await message.edit_text(config.MESSAGES["sales_duration_select"].format(service=title), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
        else:
            await _render_plans(message, source=source, catalog_id=service.id, plans=plans, back_callback=home)
        return
    rows = []
    lines = [
        config.MESSAGES["sales_service_select"],
    ]
    for service, count in services:
        rows.append([
            await _button(
                await _service_label(service),
                f"svcmarket:s:{source}:{int(service.id)}",
                fallback="🔌",
            )
        ])
    if not rows:
        lines.extend([
            "",
            config.MESSAGES["sales_empty"],
        ])
    back_cb = "back_to_admin_main" if source == "a" else "public_back_main"
    rows.append([await _button("بازگشت", back_cb, icon_key="back", fallback="⬅️")])
    await message.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@service_marketplace_router.callback_query(F.data == "admin_buy_reseller")
async def service_buy_admin(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    if config.PANEL_PROVIDER != "rebecca":
        from handlers.admin_handlers import admin_buy_reseller
        await admin_buy_reseller(callback)
        return
    await _render_purchase_services(callback.message, "a")
    await callback.answer()


@service_marketplace_router.callback_query(F.data == "public_buy_reseller")
async def service_buy_public(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    if config.PANEL_PROVIDER != "rebecca":
        from handlers.public_handlers import public_buy_reseller
        await public_buy_reseller(callback)
        return
    await _render_purchase_services(callback.message, "p")
    await callback.answer()


async def _render_plans(
    message: Message,
    *,
    source: str,
    catalog_id: int,
    plans: list[Any],
    duration_label: str | None = None,
    back_callback: str | None = None,
) -> None:
    service = await service_marketplace_service.get_service_by_catalog_id(catalog_id)
    if not service:
        await message.edit_text("❌ سرویس موردنظر دیگر فعال نیست.")
        return
    rows = []
    for plan in plans:
        callback_data = f"admin_order_{int(plan.id)}" if source == "a" else f"public_order_{int(plan.id)}"
        rows.append([
            await _button(await _plan_button_label(plan), callback_data, fallback="📦")
        ])
    if back_callback:
        back_cb = back_callback
    elif duration_label:
        back_cb = f"svcmarket:s:{source}:{catalog_id}"
    else:
        back_cb = f"svcmarket:root:{source}"
    rows.append([await _button("بازگشت", back_cb, icon_key="back", fallback="⬅️")])
    title = await _render_tokens(escape(str(service.display_name)))
    extra = f"\n🗓 {escape(duration_label)}" if duration_label else ""
    await message.edit_text(
        config.MESSAGES["sales_plan_select"].format(service=title) + extra,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@service_marketplace_router.callback_query(F.data.startswith("svcmarket:root:"))
async def service_purchase_root(callback: CallbackQuery, state: FSMContext):
    source = (callback.data or "").rsplit(":", 1)[-1]
    if source not in {"a", "p"}:
        await callback.answer("نامعتبر", show_alert=True)
        return
    await state.clear()
    await _render_purchase_services(callback.message, source)
    await callback.answer()


@service_marketplace_router.callback_query(F.data.startswith("svcmarket:s:"))
async def service_purchase_service(callback: CallbackQuery, state: FSMContext):
    parts = (callback.data or "").split(":")
    if len(parts) != 4 or parts[2] not in {"a", "p"}:
        await callback.answer("نامعتبر", show_alert=True)
        return
    source = parts[2]
    try:
        catalog_id = int(parts[3])
    except ValueError:
        await callback.answer("نامعتبر", show_alert=True)
        return
    service = await service_marketplace_service.get_service_by_catalog_id(catalog_id)
    if not service:
        await callback.answer("سرویس دیگر فعال نیست.", show_alert=True)
        return
    plans = await service_marketplace_service.plans_for_service(service.rebecca_service_id)
    if not plans:
        await callback.answer("برای این سرویس پلن فعالی وجود ندارد.", show_alert=True)
        return
    groups = service_marketplace_service.group_plans_by_duration(plans)
    grouping = await service_marketplace_service.duration_groups_enabled()
    if not grouping or len(groups) <= 1:
        await _render_plans(callback.message, source=source, catalog_id=catalog_id, plans=plans)
        await callback.answer()
        return

    rows = [
        [
            await _button(
                group.label,
                f"svcmarket:d:{source}:{catalog_id}:{group.key}",
                fallback="🗓",
            )
        ]
        for group in groups
    ]
    back = f"svcmarket:root:{source}" if len(await service_marketplace_service.sellable_services()) > 1 else ("back_to_admin_main" if source == "a" else "public_back_main")
    rows.append([await _button("بازگشت", back, icon_key="back", fallback="⬅️")])
    title = await _render_tokens(escape(str(service.display_name)))
    await callback.message.edit_text(
        config.MESSAGES["sales_duration_select"].format(service=title),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@service_marketplace_router.callback_query(F.data.startswith("svcmarket:d:"))
async def service_purchase_duration(callback: CallbackQuery):
    parts = (callback.data or "").split(":")
    if len(parts) != 5 or parts[2] not in {"a", "p"}:
        await callback.answer("نامعتبر", show_alert=True)
        return
    source = parts[2]
    try:
        catalog_id = int(parts[3])
    except ValueError:
        await callback.answer("نامعتبر", show_alert=True)
        return
    key = parts[4]
    service = await service_marketplace_service.get_service_by_catalog_id(catalog_id)
    if not service:
        await callback.answer("سرویس دیگر فعال نیست.", show_alert=True)
        return
    plans = await service_marketplace_service.plans_for_service(service.rebecca_service_id)
    groups = {item.key: item for item in service_marketplace_service.group_plans_by_duration(plans)}
    group = groups.get(key)
    if not group:
        await callback.answer("این دسته زمانی دیگر موجود نیست.", show_alert=True)
        return
    await _render_plans(
        callback.message,
        source=source,
        catalog_id=catalog_id,
        plans=list(group.plans),
        duration_label=group.label,
    )
    await callback.answer()


# ---------------------------------------------------------------------------
# Service-first free trial
# ---------------------------------------------------------------------------

@service_marketplace_router.callback_query(F.data == "svcmarket:home")
async def marketplace_home(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    if await db.is_admin_authorized(callback.from_user.id):
        from handlers.admin_handlers import get_admin_keyboard

        await callback.message.edit_text("به ربات خوش آمدید!", reply_markup=get_admin_keyboard())
    else:
        from handlers.public_handlers import get_public_main_keyboard

        await callback.message.edit_text("به ربات خوش آمدید!", reply_markup=get_public_main_keyboard())
    await callback.answer()


# ---------------------------------------------------------------------------
# SUDO settings: service mapping, automatic duration buckets, trial access
# ---------------------------------------------------------------------------

async def _render_sales_hub(message: Message) -> None:
    services = await service_marketplace_service.sellable_services()
    active_services = await __import__("rebecca_catalog").list_services(enabled_only=True)
    plans = await db.get_plans(only_active=True)
    mapped_plan_ids: set[int] = set()
    for service in active_services:
        for plan in await service_marketplace_service.plans_for_service(service.rebecca_service_id):
            mapped_plan_ids.add(int(plan.id))
    grouping = await service_marketplace_service.duration_groups_enabled()
    lines = [
        "📦 <b>ساختار فروش سرویس‌محور</b>",
        "",
        f"سرویس‌های Rebecca فعال: <b>{len(active_services)}</b>",
        f"سرویس‌های دارای پلن فروش: <b>{len(services)}</b>",
        f"پلن‌های فعال: <b>{len(plans)}</b>",
        f"پلن‌های بدون سرویس: <b>{len([p for p in plans if int(p.id) not in mapped_plan_ids])}</b>",
        "",
        "نوع پنل از سرویس Rebecca گرفته می‌شود؛ هر پلن را به WireGuard/OpenVPN/... متصل کنید.",
        "دسته زمانی از مدت خود پلن ساخته می‌شود و نیاز به تعریف دستی ندارد.",
    ]
    rows = [
        [
            await _button("افزودن پلن", "sales_add", fallback="➕"),
            await _button("اتصال پلن به سرویس", "sales_edit_services", fallback="🔌"),
        ],
        [
            await _button("سرویس‌های Rebecca", "rebecca_services", fallback="🔌"),
            await _button("حذف پلن", "sales_delete", fallback="🗑"),
        ],
        [
            await _button(
                f"دسته‌بندی زمانی: {'فعال' if grouping else 'غیرفعال'}",
                "svcmarket:duration:toggle",
                fallback="🗂",
            )
        ],
        [await _button("تنظیم تست رایگان", "cc:test", icon_key="test", fallback="🧪")],
        [await _button("بازگشت", "sudo_menu_sales", icon_key="back", fallback="⬅️")],
    ]
    await message.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@service_marketplace_router.callback_query(F.data == "sales_manage")
async def service_sales_hub(callback: CallbackQuery, state: FSMContext):
    if not _is_sudo(callback.from_user.id):
        return
    await state.clear()
    await _render_sales_hub(callback.message)
    await callback.answer()


@service_marketplace_router.callback_query(F.data == "svcmarket:duration:toggle")
async def toggle_duration_groups(callback: CallbackQuery):
    if not _is_sudo(callback.from_user.id):
        await callback.answer("غیرمجاز", show_alert=True)
        return
    current = await service_marketplace_service.duration_groups_enabled()
    await service_marketplace_service.set_duration_groups_enabled(not current)
    await _render_sales_hub(callback.message)
    await callback.answer("بروزرسانی شد")


async def _render_trial_admin_center(message: Message) -> None:
    config_settings = await operations_service.get_trial_settings()
    panel_settings = await trial_experience_service.get_panel_trial_settings()
    services = await __import__("rebecca_catalog").list_services(enabled_only=True)
    lines = [
        "🧪 <b>مرکز تست رایگان</b>",
        "",
        f"کانفیگ تست: {'✅ فعال' if config_settings['enabled'] else '⛔ غیرفعال'}",
        f"تست پنل: {'✅ فعال' if panel_settings['enabled'] else '⛔ غیرفعال'}",
        f"سرویس‌های Rebecca فعال: <b>{len(services)}</b>",
        "",
        "انتخاب سرویس برای کاربر مستقیماً از کاتالوگ Rebecca انجام می‌شود؛ دیگر به انتخاب پلن برای پیدا کردن Service ID وابسته نیست.",
    ]
    rows = [
        [await _button("تنظیم کانفیگ تست", "ux:trialadmin:config", icon_key="test", fallback="🧪")],
        [await _button("تنظیم تست پنل", "ux:trialadmin:panel", icon_key="panel", fallback="🧩")],
        [await _button("سرویس‌های قابل تست", "ux:trialadmin:plans", fallback="🔌")],
        [await _button("خانه", "back_to_main", icon_key="home", fallback="🏠")],
    ]
    await message.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@service_marketplace_router.callback_query(F.data == "cc:test")
async def service_trial_admin_center(callback: CallbackQuery, state: FSMContext):
    if config.PANEL_PROVIDER != "rebecca":
        from handlers.trial_experience import trial_center
        await trial_center(callback, state)
        return
    if not _is_sudo(callback.from_user.id):
        return
    await state.clear()
    await _render_trial_admin_center(callback.message)
    await callback.answer()


async def _render_service_trial_access(message: Message) -> None:
    services = await __import__("rebecca_catalog").list_services(enabled_only=True)
    rows = []
    lines = [
        "🔌 <b>سرویس‌های قابل تست</b>",
        "",
        "برای هر سرویس مشخص کنید تست پنل و تست کانفیگ در دسترس باشد یا نه.",
        "",
    ]
    for service in services:
        panel_on = await service_marketplace_service.trial_access(service.rebecca_service_id, "panel")
        config_on = await service_marketplace_service.trial_access(service.rebecca_service_id, "config")
        lines.append(f"• {escape(str(service.display_name))}")
        rows.append([
            await _button(
                f"{'✅' if panel_on else '⛔'} پنل",
                f"svcmarket:trialaccess:panel:{int(service.rebecca_service_id)}",
                fallback="🧩",
            ),
            await _button(
                f"{'✅' if config_on else '⛔'} کانفیگ",
                f"svcmarket:trialaccess:config:{int(service.rebecca_service_id)}",
                fallback="🧪",
            ),
        ])
    if not services:
        lines.append("هیچ سرویس Rebecca فعالی ثبت نشده است.")
    rows.append([await _button("بازگشت", "cc:test", icon_key="back", fallback="⬅️")])
    await message.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@service_marketplace_router.callback_query(F.data == "ux:trialadmin:plans")
async def service_trial_access_list(callback: CallbackQuery):
    if config.PANEL_PROVIDER != "rebecca":
        from handlers.trial_experience import trial_plan_access
        await trial_plan_access(callback)
        return
    if not _is_sudo(callback.from_user.id):
        return
    await _render_service_trial_access(callback.message)
    await callback.answer()


@service_marketplace_router.callback_query(F.data.startswith("svcmarket:trialaccess:"))
async def service_trial_access_toggle(callback: CallbackQuery):
    if not _is_sudo(callback.from_user.id):
        await callback.answer("غیرمجاز", show_alert=True)
        return
    parts = (callback.data or "").split(":")
    if len(parts) != 4 or parts[2] not in {"panel", "config"}:
        await callback.answer("نامعتبر", show_alert=True)
        return
    trial_type = parts[2]
    try:
        service_id = int(parts[3])
    except ValueError:
        await callback.answer("نامعتبر", show_alert=True)
        return
    current = await service_marketplace_service.trial_access(service_id, trial_type)
    try:
        await service_marketplace_service.set_trial_access(service_id, trial_type, not current)
    except OperationsError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await _render_service_trial_access(callback.message)
    await callback.answer("بروزرسانی شد")
