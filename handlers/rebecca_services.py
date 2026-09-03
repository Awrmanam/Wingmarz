"""Rebecca service-catalog Telegram workflow.

This router is registered explicitly before the legacy sudo router. It intercepts
only Rebecca-specific callbacks that replace raw Service-ID entry; Marzban mode
continues through the original sudo handlers unchanged.
"""
from __future__ import annotations

from html import escape

from aiogram import F, Router
from aiogram.filters import BaseFilter, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import config
from database import db
from models.schemas import PlanModel
from rebecca_api import RebeccaAPIError
from rebecca_catalog import (
    RebeccaCatalogDuplicate,
    RebeccaDiscoveryError,
    RebeccaDiscoveryNotFound,
    discover_services_for_user,
    discovery_confirmation_html,
    get_service,
    get_service_by_rebecca_id,
    import_services_atomic,
    list_services,
    remove_service,
    rename_service,
    service_ids_from_catalog_ids,
    set_service_enabled,
)
from utils.rebecca import parse_service_ids

# sudo_handlers is imported first by bot.py, so these shared state classes are
# already defined when this module is imported. The legacy message handlers are
# intentionally retained for the explicit manual/raw-ID fallback.
from handlers.sudo_handlers import CreatePlanStates, EditPlanServicesStates


rebecca_services_router = Router(name="rebecca_services")


class RebeccaOnly(BaseFilter):
    async def __call__(self, _event) -> bool:
        return config.PANEL_PROVIDER == "rebecca"


class RebeccaServiceStates(StatesGroup):
    waiting_for_discovery_username = State()
    choosing_display_name = State()
    waiting_for_display_name = State()
    waiting_for_add_confirmation = State()
    waiting_for_rename = State()


class RebeccaPlanSelectStates(StatesGroup):
    choosing = State()


def _authorized(user_id: int) -> bool:
    return user_id in config.SUDO_ADMINS


async def _deny(callback: CallbackQuery) -> bool:
    if _authorized(callback.from_user.id):
        return False
    await callback.answer("غیرمجاز", show_alert=True)
    return True


def _parse_callback_id(callback: CallbackQuery) -> int | None:
    try:
        return int((callback.data or "").rsplit(":", 1)[-1])
    except (TypeError, ValueError):
        return None


def _service_menu_keyboard(services) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for service in services:
        status = "✅" if service.is_enabled else "⛔"
        rows.append([
            InlineKeyboardButton(
                text=f"{status} {service.display_name} · #{service.rebecca_service_id}",
                callback_data=f"rsvc:v:{service.id}",
            )
        ])
    rows.append([InlineKeyboardButton(text="➕ افزودن سرویس", callback_data="rsvc:add")])
    rows.append([InlineKeyboardButton(text="⬅️ فروش و مالی", callback_data="sudo_menu_sales")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _render_service_menu(message: Message) -> None:
    services = await list_services()
    enabled = sum(1 for item in services if item.is_enabled)
    text = (
        "🔌 <b>سرویس‌های Rebecca</b>\n\n"
        f"کل سرویس‌ها: <b>{len(services)}</b>\n"
        f"فعال: <b>{enabled}</b>\n\n"
        "برای افزودن سرویس لازم نیست Service ID را بدانید. یک کانفیگ آزمایشی داخل "
        "سرویس موردنظر بسازید و Username آن را برای ربات بفرستید."
    )
    await message.edit_text(text, reply_markup=_service_menu_keyboard(services))


async def _send_service_menu(message: Message) -> None:
    services = await list_services()
    enabled = sum(1 for item in services if item.is_enabled)
    text = (
        "🔌 <b>سرویس‌های Rebecca</b>\n\n"
        f"کل سرویس‌ها: <b>{len(services)}</b>\n"
        f"فعال: <b>{enabled}</b>\n\n"
        "برای افزودن سرویس لازم نیست Service ID را بدانید. یک کانفیگ آزمایشی داخل "
        "سرویس موردنظر بسازید و Username آن را برای ربات بفرستید."
    )
    await message.answer(text, reply_markup=_service_menu_keyboard(services))


async def _render_service_detail(message: Message, internal_id: int) -> bool:
    service = await get_service(internal_id)
    if not service:
        return False
    status = "فعال ✅" if service.is_enabled else "غیرفعال ⛔"
    text = (
        f"🔌 <b>{escape(service.display_name)}</b>\n\n"
        f"🆔 Rebecca Service ID: <code>{service.rebecca_service_id}</code>\n"
        f"🏷 نام اصلی: {escape(str(service.provider_name or '-'))}\n"
        f"👤 کانفیگ مرجع: <code>{escape(str(service.source_username or '-'))}</code>\n"
        f"وضعیت: {status}"
    )
    toggle_text = "⛔ غیرفعال کردن" if service.is_enabled else "✅ فعال کردن"
    await message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ تغییر نام", callback_data=f"rsvc:ren:{service.id}")],
        [InlineKeyboardButton(text=toggle_text, callback_data=f"rsvc:tog:{service.id}")],
        [InlineKeyboardButton(text="🗑 حذف از کاتالوگ", callback_data=f"rsvc:rm:{service.id}")],
        [InlineKeyboardButton(text="⬅️ لیست سرویس‌ها", callback_data="rebecca_services")],
    ]))
    return True


@rebecca_services_router.message(Command("rebecca_services"), RebeccaOnly())
async def rebecca_services_command(message: Message, state: FSMContext):
    if not _authorized(message.from_user.id):
        return
    await state.clear()
    await _send_service_menu(message)


@rebecca_services_router.callback_query(F.data == "sudo_menu_sales", RebeccaOnly())
async def rebecca_sales_menu(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    await state.clear()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛒 مدیریت فروش", callback_data="sales_manage")],
        [InlineKeyboardButton(text="🔌 سرویس‌های Rebecca", callback_data="rebecca_services")],
        [
            InlineKeyboardButton(text=config.BUTTONS["sales_cards"], callback_data="sales_cards"),
            InlineKeyboardButton(text=config.BUTTONS["set_billing"], callback_data="set_billing"),
        ],
        [InlineKeyboardButton(text=config.BUTTONS["set_login_url"], callback_data="set_login_url")],
        [InlineKeyboardButton(text=config.BUTTONS["back"], callback_data="back_to_main")],
    ])
    await callback.message.edit_text("💳 فروش و مالی:", reply_markup=kb)
    await callback.answer()


@rebecca_services_router.callback_query(F.data == "rebecca_services", RebeccaOnly())
async def rebecca_services_menu(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    await state.clear()
    await _render_service_menu(callback.message)
    await callback.answer()


@rebecca_services_router.callback_query(F.data == "rsvc:add", RebeccaOnly())
async def rebecca_service_add(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    await state.clear()
    await state.set_state(RebeccaServiceStates.waiting_for_discovery_username)
    await callback.message.edit_text(
        "➕ <b>افزودن سرویس Rebecca</b>\n\n"
        "داخل سرویس موردنظر در پنل Rebecca یک کانفیگ/کاربر آزمایشی بساز.\n"
        "سپس فقط <b>نام کاربری همان کانفیگ</b> را اینجا ارسال کن.\n\n"
        "ربات فقط اطلاعات کاربر را می‌خواند و هیچ تغییری در پنل ایجاد نمی‌کند.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ لغو", callback_data="rsvc:cancel")]
        ]),
    )
    await callback.answer()


@rebecca_services_router.message(RebeccaServiceStates.waiting_for_discovery_username, F.text)
async def rebecca_discovery_username(message: Message, state: FSMContext):
    if not _authorized(message.from_user.id) or config.PANEL_PROVIDER != "rebecca":
        return
    username = message.text.strip()
    try:
        result = await discover_services_for_user(username)
    except RebeccaDiscoveryNotFound:
        await message.answer("❌ کانفیگ/کاربری با این نام در Rebecca پیدا نشد. Username را دقیق بررسی کن.")
        return
    except RebeccaDiscoveryError:
        await message.answer("❌ پاسخ Rebecca برای شناسایی سرویس معتبر نبود. چیزی ذخیره نشد.")
        return
    except RebeccaAPIError as exc:
        code = f" (HTTP {exc.status_code})" if exc.status_code else ""
        await message.answer(f"⚠️ ارتباط با Rebecca برای شناسایی سرویس ناموفق بود{code}. چیزی ذخیره نشد.")
        return

    services = result.get("services") or []
    if len(services) != 1:
        await message.answer("❌ Rebecca تعداد غیرمنتظره‌ای سرویس برای این کاربر برگرداند. چیزی ذخیره نشد.")
        return

    detected = services[0]
    service_id = detected["service_id"]
    provider_name = detected.get("service_name")
    existing = await get_service_by_rebecca_id(service_id)
    if existing:
        await state.clear()
        await message.answer(
            f"ℹ️ این سرویس قبلاً با نام <b>{escape(existing.display_name)}</b> ثبت شده است.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="مشاهده سرویس", callback_data=f"rsvc:v:{existing.id}")],
                [InlineKeyboardButton(text="⬅️ لیست سرویس‌ها", callback_data="rebecca_services")],
            ]),
        )
        return

    suggested = (provider_name or f"Service {service_id}").strip() or f"Service {service_id}"
    await state.update_data(
        discovery_username=result["username"],
        discovery_service_id=service_id,
        discovery_provider_name=provider_name,
        discovery_display_name=suggested,
    )
    await state.set_state(RebeccaServiceStates.choosing_display_name)
    await message.answer(
        discovery_confirmation_html(result["username"], service_id, provider_name)
        + "\n\nیک نام نمایشی برای استفاده داخل ربات انتخاب کن.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"✅ استفاده از «{suggested[:28]}»", callback_data="radd:default")],
            [InlineKeyboardButton(text="✏️ نام دلخواه می‌فرستم", callback_data="radd:custom")],
            [InlineKeyboardButton(text="❌ لغو", callback_data="rsvc:cancel")],
        ]),
    )


async def _show_add_confirmation(message: Message, state: FSMContext) -> bool:
    data = await state.get_data()
    if "discovery_service_id" not in data or not data.get("discovery_display_name"):
        return False
    display_name = str(data["discovery_display_name"]).strip()
    provider_name = data.get("discovery_provider_name")
    text = (
        "✅ <b>تأیید نهایی ثبت سرویس</b>\n\n"
        f"👤 کانفیگ مرجع: <code>{escape(str(data.get('discovery_username') or '-'))}</code>\n"
        f"🆔 Service ID: <code>{int(data['discovery_service_id'])}</code>\n"
        f"🏷 نام Rebecca: {escape(str(provider_name or 'نام ثبت نشده'))}\n"
        f"📝 نام نمایشی: <b>{escape(display_name)}</b>\n\n"
        "با تأیید، فقط این شناسه در کاتالوگ محلی ربات ذخیره می‌شود."
    )
    await state.set_state(RebeccaServiceStates.waiting_for_add_confirmation)
    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ ثبت سرویس", callback_data="radd:confirm")],
        [InlineKeyboardButton(text="❌ لغو", callback_data="rsvc:cancel")],
    ]))
    return True


@rebecca_services_router.callback_query(F.data == "radd:default", RebeccaOnly())
async def rebecca_add_default_name(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    if not await _show_add_confirmation(callback.message, state):
        await callback.answer("اطلاعات شناسایی منقضی شده؛ دوباره سرویس را اضافه کن.", show_alert=True)
        return
    await callback.answer()


@rebecca_services_router.callback_query(F.data == "radd:custom", RebeccaOnly())
async def rebecca_add_custom_name(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    data = await state.get_data()
    if "discovery_service_id" not in data:
        await callback.answer("اطلاعات شناسایی منقضی شده؛ دوباره سرویس را اضافه کن.", show_alert=True)
        return
    await state.set_state(RebeccaServiceStates.waiting_for_display_name)
    await callback.message.answer("✏️ نام نمایشی دلخواه سرویس را ارسال کن:")
    await callback.answer()


@rebecca_services_router.message(RebeccaServiceStates.waiting_for_display_name, F.text)
async def rebecca_add_display_name(message: Message, state: FSMContext):
    if not _authorized(message.from_user.id) or config.PANEL_PROVIDER != "rebecca":
        return
    name = message.text.strip()
    if not name or len(name) > 80:
        await message.answer("نام نمایشی باید بین ۱ تا ۸۰ کاراکتر باشد.")
        return
    await state.update_data(discovery_display_name=name)
    if not await _show_add_confirmation(message, state):
        await state.clear()
        await message.answer("❌ اطلاعات شناسایی منقضی شده؛ دوباره سرویس را اضافه کن.")


@rebecca_services_router.callback_query(F.data == "radd:confirm", RebeccaOnly())
async def rebecca_add_confirm(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    data = await state.get_data()
    try:
        created = await import_services_atomic(
            [{
                "service_id": int(data["discovery_service_id"]),
                "display_name": str(data["discovery_display_name"]),
                "provider_name": data.get("discovery_provider_name"),
            }],
            source_username=str(data.get("discovery_username") or "") or None,
        )
    except (KeyError, ValueError):
        await callback.answer("اطلاعات شناسایی ناقص است؛ دوباره سرویس را اضافه کن.", show_alert=True)
        return
    except RebeccaCatalogDuplicate:
        await state.clear()
        await callback.message.edit_text("ℹ️ این Service ID قبلاً در کاتالوگ ثبت شده است.")
        await callback.answer()
        return

    await state.clear()
    service = created[0]
    await callback.message.edit_text(
        f"✅ سرویس <b>{escape(service.display_name)}</b> با Service ID <code>{service.rebecca_service_id}</code> ثبت شد.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔌 لیست سرویس‌ها", callback_data="rebecca_services")]
        ]),
    )
    await callback.answer()


@rebecca_services_router.callback_query(F.data == "rsvc:cancel", RebeccaOnly())
async def rebecca_service_cancel(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    await state.clear()
    await _render_service_menu(callback.message)
    await callback.answer()


@rebecca_services_router.callback_query(F.data.startswith("rsvc:v:"), RebeccaOnly())
async def rebecca_service_detail(callback: CallbackQuery):
    if await _deny(callback):
        return
    internal_id = _parse_callback_id(callback)
    if internal_id is None:
        await callback.answer("شناسه نامعتبر است.", show_alert=True)
        return
    if not await _render_service_detail(callback.message, internal_id):
        await callback.answer("سرویس پیدا نشد.", show_alert=True)
        return
    await callback.answer()


@rebecca_services_router.callback_query(F.data.startswith("rsvc:ren:"), RebeccaOnly())
async def rebecca_service_rename_start(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    internal_id = _parse_callback_id(callback)
    if internal_id is None or not await get_service(internal_id):
        await callback.answer("سرویس پیدا نشد.", show_alert=True)
        return
    await state.clear()
    await state.update_data(rename_service_id=internal_id)
    await state.set_state(RebeccaServiceStates.waiting_for_rename)
    await callback.message.answer("نام نمایشی جدید را ارسال کن:")
    await callback.answer()


@rebecca_services_router.message(RebeccaServiceStates.waiting_for_rename, F.text)
async def rebecca_service_rename_value(message: Message, state: FSMContext):
    if not _authorized(message.from_user.id) or config.PANEL_PROVIDER != "rebecca":
        return
    name = message.text.strip()
    if not name or len(name) > 80:
        await message.answer("نام نمایشی باید بین ۱ تا ۸۰ کاراکتر باشد.")
        return
    data = await state.get_data()
    internal_id = data.get("rename_service_id")
    ok = bool(internal_id) and await rename_service(int(internal_id), name)
    await state.clear()
    await message.answer(
        "✅ نام سرویس تغییر کرد." if ok else "❌ سرویس پیدا نشد.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔌 لیست سرویس‌ها", callback_data="rebecca_services")]
        ]),
    )


@rebecca_services_router.callback_query(F.data.startswith("rsvc:tog:"), RebeccaOnly())
async def rebecca_service_toggle(callback: CallbackQuery):
    if await _deny(callback):
        return
    internal_id = _parse_callback_id(callback)
    if internal_id is None:
        await callback.answer("شناسه نامعتبر است.", show_alert=True)
        return
    service = await get_service(internal_id)
    if not service:
        await callback.answer("سرویس پیدا نشد.", show_alert=True)
        return
    await set_service_enabled(internal_id, not service.is_enabled)
    await _render_service_detail(callback.message, internal_id)
    await callback.answer("وضعیت بروزرسانی شد.")


@rebecca_services_router.callback_query(F.data.startswith("rsvc:rm:"), RebeccaOnly())
async def rebecca_service_remove_confirm(callback: CallbackQuery):
    if await _deny(callback):
        return
    internal_id = _parse_callback_id(callback)
    if internal_id is None:
        await callback.answer("شناسه نامعتبر است.", show_alert=True)
        return
    service = await get_service(internal_id)
    if not service:
        await callback.answer("سرویس پیدا نشد.", show_alert=True)
        return
    await callback.message.edit_text(
        f"🗑 سرویس <b>{escape(service.display_name)}</b> از کاتالوگ حذف شود؟\n\n"
        "پلن‌ها و سفارش‌های قدیمی دست‌نخورده می‌مانند و Service ID تاریخی آن‌ها حذف نمی‌شود.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ بله، حذف", callback_data=f"rsvc:rmok:{service.id}")],
            [InlineKeyboardButton(text="❌ خیر", callback_data=f"rsvc:v:{service.id}")],
        ]),
    )
    await callback.answer()


@rebecca_services_router.callback_query(F.data.startswith("rsvc:rmok:"), RebeccaOnly())
async def rebecca_service_remove(callback: CallbackQuery):
    if await _deny(callback):
        return
    internal_id = _parse_callback_id(callback)
    if internal_id is None:
        await callback.answer("شناسه نامعتبر است.", show_alert=True)
        return
    ok = await remove_service(internal_id)
    await _render_service_menu(callback.message)
    await callback.answer("حذف شد." if ok else "سرویس قبلاً حذف شده بود.")


def _plan_selector_keyboard(services, selected: set[int]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for item in services:
        mark = "✅" if item.id in selected else "⬜"
        rows.append([
            InlineKeyboardButton(text=f"{mark} {item.display_name}", callback_data=f"rcp:t:{item.id}")
        ])
    rows.append([InlineKeyboardButton(text="✅ تایید انتخاب", callback_data="rcp:done")])
    rows.append([InlineKeyboardButton(text="🧰 ورود دستی Service ID", callback_data="rcp:manual")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _render_plan_selector(message: Message, state: FSMContext) -> bool:
    services = await list_services(enabled_only=True)
    data = await state.get_data()
    selected = {int(item) for item in data.get("rebecca_catalog_selected", [])}
    if not services:
        if data.get("rebecca_catalog_mode") == "edit":
            await state.set_state(EditPlanServicesStates.waiting_for_services)
        else:
            await state.set_state(CreatePlanStates.waiting_for_rebecca_services)
        await message.edit_text(
            "هیچ سرویس فعالی در کاتالوگ Rebecca ثبت نشده است.\n"
            "فعلاً Service IDهای عددی را با کاما وارد کن یا ابتدا از بخش «سرویس‌های Rebecca» سرویس اضافه کن."
        )
        return False
    await message.edit_text(
        "🔌 <b>سرویس‌های این پلن را انتخاب کنید</b>\n\n"
        "می‌توانی چند سرویس را انتخاب کنی و در پایان «تایید انتخاب» را بزنی.",
        reply_markup=_plan_selector_keyboard(services, selected),
    )
    return True


@rebecca_services_router.callback_query(F.data.startswith("sales_renew_mode_"), RebeccaOnly())
async def rebecca_plan_create_services(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    mode = (callback.data or "").split("_")[-1]
    if mode not in {"incremental", "full"}:
        await callback.answer("حالت تمدید نامعتبر است.", show_alert=True)
        return
    await state.update_data(
        allow_incremental_renewal=(mode == "incremental"),
        rebecca_catalog_mode="create",
        rebecca_catalog_selected=[],
        rebecca_catalog_legacy_ids=[],
    )
    await state.set_state(RebeccaPlanSelectStates.choosing)
    await _render_plan_selector(callback.message, state)
    await callback.answer()


@rebecca_services_router.callback_query(F.data.startswith("sales_edit_service_"), RebeccaOnly())
async def rebecca_plan_edit_services(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    try:
        plan_id = int((callback.data or "").rsplit("_", 1)[-1])
    except ValueError:
        await callback.answer("پلن نامعتبر است.", show_alert=True)
        return
    plan = await db.get_plan_by_id(plan_id)
    if not plan:
        await callback.answer("پلن پیدا نشد.", show_alert=True)
        return
    try:
        existing_ids = list(parse_service_ids(getattr(plan, "rebecca_service_ids", "") or ""))
    except ValueError:
        existing_ids = []
    active = await list_services(enabled_only=True)
    selected = {item.id for item in active if item.rebecca_service_id in existing_ids}
    selected_provider_ids = {item.rebecca_service_id for item in active if item.id in selected}
    legacy_ids = [service_id for service_id in existing_ids if service_id not in selected_provider_ids]
    await state.clear()
    await state.update_data(
        edit_service_plan_id=plan_id,
        rebecca_catalog_mode="edit",
        rebecca_catalog_selected=sorted(selected),
        rebecca_catalog_legacy_ids=legacy_ids,
    )
    await state.set_state(RebeccaPlanSelectStates.choosing)
    await _render_plan_selector(callback.message, state)
    await callback.answer()


@rebecca_services_router.callback_query(F.data.startswith("rcp:t:"), RebeccaOnly())
async def rebecca_plan_toggle_service(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    data = await state.get_data()
    if data.get("rebecca_catalog_mode") not in {"create", "edit"}:
        await callback.answer("این انتخاب منقضی شده است.", show_alert=True)
        return
    internal_id = _parse_callback_id(callback)
    if internal_id is None:
        await callback.answer("شناسه نامعتبر است.", show_alert=True)
        return
    service = await get_service(internal_id)
    if not service or not service.is_enabled:
        await callback.answer("این سرویس دیگر فعال نیست.", show_alert=True)
        return
    selected = {int(item) for item in data.get("rebecca_catalog_selected", [])}
    if internal_id in selected:
        selected.remove(internal_id)
    else:
        selected.add(internal_id)
    await state.update_data(rebecca_catalog_selected=sorted(selected))
    await _render_plan_selector(callback.message, state)
    await callback.answer()


@rebecca_services_router.callback_query(F.data == "rcp:manual", RebeccaOnly())
async def rebecca_plan_manual_fallback(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    data = await state.get_data()
    mode = data.get("rebecca_catalog_mode")
    if mode == "edit":
        await state.set_state(EditPlanServicesStates.waiting_for_services)
    elif mode == "create":
        await state.set_state(CreatePlanStates.waiting_for_rebecca_services)
    else:
        await callback.answer("این انتخاب منقضی شده است.", show_alert=True)
        return
    await callback.message.edit_text(
        "🧰 حالت دستی/اضطراری\n\nService IDهای عددی Rebecca را با کاما وارد کن (مثال: 1,2)."
    )
    await callback.answer()


async def _save_rebecca_plan_from_state(callback: CallbackQuery, state: FSMContext, canonical: str) -> None:
    data = await state.get_data()
    plan = PlanModel(
        name=data.get("name"),
        plan_type=data.get("plan_type", "volume"),
        traffic_limit_bytes=data.get("traffic_limit_bytes"),
        time_limit_seconds=data.get("time_limit_seconds"),
        max_users=data.get("max_users"),
        price=data.get("price", 0),
        is_active=True,
        allow_incremental_renewal=data.get("allow_incremental_renewal", True),
        rebecca_service_ids=canonical,
    )
    ok = await db.add_plan(plan)
    await state.clear()
    await callback.message.edit_text("✅ پلن با موفقیت اضافه شد." if ok else "❌ خطا در افزودن پلن.")


@rebecca_services_router.callback_query(F.data == "rcp:done", RebeccaOnly())
async def rebecca_plan_catalog_done(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    data = await state.get_data()
    mode = data.get("rebecca_catalog_mode")
    if mode not in {"create", "edit"}:
        await callback.answer("این انتخاب منقضی شده است.", show_alert=True)
        return
    selected_catalog = [int(item) for item in data.get("rebecca_catalog_selected", [])]
    legacy_ids = [int(item) for item in data.get("rebecca_catalog_legacy_ids", [])]
    if not selected_catalog and not legacy_ids:
        await callback.answer("حداقل یک سرویس انتخاب کن.", show_alert=True)
        return
    try:
        selected_provider = await service_ids_from_catalog_ids(selected_catalog)
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    final_ids: list[int] = []
    for service_id in [*legacy_ids, *selected_provider]:
        if service_id not in final_ids:
            final_ids.append(service_id)
    canonical = ",".join(str(item) for item in final_ids)

    if mode == "edit":
        plan_id = int(data["edit_service_plan_id"])
        ok = await db.update_plan(plan_id, rebecca_service_ids=canonical)
        await state.clear()
        await callback.message.edit_text(
            "✅ سرویس‌های پلن بروزرسانی شد." if ok else "❌ خطا در بروزرسانی پلن."
        )
        await callback.answer()
        return

    await _save_rebecca_plan_from_state(callback, state, canonical)
    await callback.answer()
