"""Rebecca service-catalog Telegram workflow.

This router is placed before the legacy sudo router by handlers.__init__ so it
can replace only Rebecca-specific raw-ID prompts while leaving Marzban behavior
untouched.
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

# Importing this module happens only after handlers.sudo_handlers has completed.
from handlers.sudo_handlers import (  # noqa: E402
    CreatePlanStates,
    EditPlanServicesStates,
    _save_sales_plan,
)


rebecca_services_router = Router(name="rebecca_services")


class RebeccaOnly(BaseFilter):
    async def __call__(self, _event) -> bool:
        return config.PANEL_PROVIDER == "rebecca"


class RebeccaServiceStates(StatesGroup):
    waiting_for_discovery_username = State()
    waiting_for_display_name = State()
    waiting_for_rename = State()


def _authorized(user_id: int) -> bool:
    return user_id in config.SUDO_ADMINS


async def _deny(callback: CallbackQuery) -> bool:
    if _authorized(callback.from_user.id):
        return False
    await callback.answer("غیرمجاز", show_alert=True)
    return True


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


async def _render_service_menu(target: Message | CallbackQuery) -> None:
    services = await list_services()
    enabled = sum(1 for item in services if item.is_enabled)
    text = (
        "🔌 <b>سرویس‌های Rebecca</b>\n\n"
        f"کل سرویس‌ها: <b>{len(services)}</b>\n"
        f"فعال: <b>{enabled}</b>\n\n"
        "برای افزودن سرویس لازم نیست Service ID را بدانید. یک کانفیگ آزمایشی داخل "
        "سرویس موردنظر بسازید و Username آن را برای ربات بفرستید."
    )
    markup = _service_menu_keyboard(services)
    if isinstance(target, CallbackQuery):
        await target.message.edit_text(text, reply_markup=markup)
        await target.answer()
    else:
        await target.answer(text, reply_markup=markup)


@rebecca_services_router.message(Command("rebecca_services"), RebeccaOnly())
async def rebecca_services_command(message: Message, state: FSMContext):
    if not _authorized(message.from_user.id):
        return
    await state.clear()
    await _render_service_menu(message)


# Preserve the existing sales menu in Rebecca mode, adding one catalog entry.
# In non-Rebecca mode this filter does not match and the legacy sudo handler runs.
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
    await _render_service_menu(callback)


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
    # Rebecca currently returns exactly one service per user. Keep the guard strict.
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
    await state.set_state(RebeccaServiceStates.waiting_for_display_name)
    await message.answer(
        discovery_confirmation_html(result["username"], service_id, provider_name)
        + "\n\nیک نام نمایشی برای استفاده داخل ربات انتخاب کن.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"✅ استفاده از «{suggested[:28]}»", callback_data="radd:default")],
            [InlineKeyboardButton(text="✏️ نام دلخواه می‌فرستم", callback_data="radd:custom")],
            [InlineKeyboardButton(text="❌ لغو", callback_data="rsvc:cancel")],
        ]),
    )


async def _show_add_confirmation(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    display_name = str(data.get("discovery_display_name") or "").strip()
    provider_name = data.get("discovery_provider_name")
    text = (
        "✅ <b>تأیید نهایی ثبت سرویس</b>\n\n"
        f"👤 کانفیگ مرجع: <code>{escape(str(data.get('discovery_username') or '-'))}</code>\n"
        f"🆔 Service ID: <code>{int(data['discovery_service_id'])}</code>\n"
        f"🏷 نام Rebecca: {escape(str(provider_name or 'نام ثبت نشده'))}\n"
        f"📝 نام نمایشی: <b>{escape(display_name)}</b>\n\n"
        "با تأیید، فقط این شناسه در کاتالوگ محلی ربات ذخیره می‌شود."
    )
    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ ثبت سرویس", callback_data="radd:confirm")],
        [InlineKeyboardButton(text="❌ لغو", callback_data="rsvc:cancel")],
    ]))


@rebecca_services_router.callback_query(F.data == "radd:default", RebeccaOnly())
async def rebecca_add_default_name(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    await _show_add_confirmation(callback.message, state)
    await callback.answer()


@rebecca_services_router.callback_query(F.data == "radd:custom", RebeccaOnly())
async def rebecca_add_custom_name(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
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
    await _show_add_confirmation(message, state)


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
    await _render_service_menu(callback)


@rebecca_services_router.callback_query(F.data.startswith("rsvc:v:"), RebeccaOnly())
async def rebecca_service_detail(callback: CallbackQuery):
    if await _deny(callback):
        return
    try:
        internal_id = int(callback.data.rsplit(":", 1)[-1])
    except ValueError:
        await callback.answer("شناسه نامعتبر است.", show_alert=True)
        return
    service = await get_service(internal_id)
    if not service:
        await callback.answer("سرویس پیدا نشد.", show_alert=True)
        return
    status = "فعال ✅" if service.is_enabled else "غیرفعال ⛔"
    text = (
        f"🔌 <b>{escape(service.display_name)}</b>\n\n"
        f"🆔 Rebecca Service ID: <code>{service.rebecca_service_id}</code>\n"
        f"🏷 نام اصلی: {escape(str(service.provider_name or '-'))}\n"
        f"👤 کانفیگ مرجع: <code>{escape(str(service.source_username or '-'))}</code>\n"
        f"وضعیت: {status}"
    )
    toggle_text = "⛔ غیرفعال کردن" if service.is_enabled else "✅ فعال کردن"
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ تغییر نام", callback_data=f"rsvc:ren:{service.id}")],
        [InlineKeyboardButton(text=toggle_text, callback_data=f"rsvc:tog:{service.id}")],
        [InlineKeyboardButton(text="🗑 حذف از کاتالوگ", callback_data=f"rsvc:rm:{service.id}")],
        [InlineKeyboardButton(text="⬅️ لیست سرویس‌ها", callback_data="rebecca_services")],
    ]))
    await callback.answer()


@rebecca_services_router.callback_query(F.data.startswith("rsvc:ren:"), RebeccaOnly())
async def rebecca_service_rename_start(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    internal_id = int(callback.data.rsplit(":", 1)[-1])
    if not await get_service(internal_id):
        await callback.answer("سرویس پیدا نشد.", show_alert=True)
        return
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
    ok = await rename_service(int(data["rename_service_id"]), name)
    await state.clear()
    await message.answer("✅ نام سرویس تغییر کرد." if ok else "❌ سرویس پیدا نشد.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔌 لیست سرویس‌ها", callback_data="rebecca_services")]
    ]))


@rebecca_services_router.callback_query(F.data.startswith("rsvc:tog:"), RebeccaOnly())
async def rebecca_service_toggle(callback: CallbackQuery):
    if await _deny(callback):
        return
    internal_id = int(callback.data.rsplit(":", 1)[-1])
    service = await get_service(internal_id)
    if not service:
        await callback.answer("سرویس پیدا نشد.", show_alert=True)
        return
    await set_service_enabled(internal_id, not service.is_enabled)
    await callback.answer("وضعیت بروزرسانی شد.")
    # Re-render through the same detail callback data.
    callback.data = f"rsvc:v:{internal_id}"
    await rebecca_service_detail(callback)


@rebecca_services_router.callback_query(F.data.startswith("rsvc:rm:"), RebeccaOnly())
async def rebecca_service_remove_confirm(callback: CallbackQuery):
    if await _deny(callback):
        return
    internal_id = int(callback.data.rsplit(":", 1)[-1])
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
    internal_id = int(callback.data.rsplit(":", 1)[-1])
    ok = await remove_service(internal_id)
    await callback.answer("حذف شد." if ok else "سرویس پیدا نشد.")
    await _render_service_menu(callback)


def _plan_selector_keyboard(services, selected: set[int], *, allow_manual: bool = True) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for item in services:
        mark = "✅" if item.id in selected else "⬜"
        rows.append([
            InlineKeyboardButton(text=f"{mark} {item.display_name}", callback_data=f"rcp:t:{item.id}")
        ])
    rows.append([InlineKeyboardButton(text="✅ تایید انتخاب", callback_data="rcp:done")])
    if allow_manual:
        rows.append([InlineKeyboardButton(text="🧰 ورود دستی Service ID", callback_data="rcp:manual")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _render_plan_selector(callback: CallbackQuery, state: FSMContext) -> None:
    services = await list_services(enabled_only=True)
    data = await state.get_data()
    selected = {int(item) for item in data.get("rebecca_catalog_selected", [])}
    if not services:
        mode = data.get("rebecca_catalog_mode")
        if mode == "edit":
            await state.set_state(EditPlanServicesStates.waiting_for_services)
        else:
            await state.set_state(CreatePlanStates.waiting_for_rebecca_services)
        await callback.message.edit_text(
            "هیچ سرویس فعالی در کاتالوگ Rebecca ثبت نشده است.\n"
            "فعلاً Service IDهای عددی را با کاما وارد کن یا ابتدا از بخش «سرویس‌های Rebecca» سرویس اضافه کن."
        )
        await callback.answer()
        return
    await callback.message.edit_text(
        "🔌 <b>سرویس‌های این پلن را انتخاب کنید</b>\n\n"
        "می‌توانی چند سرویس را انتخاب کنی و در پایان «تایید انتخاب» را بزنی.",
        reply_markup=_plan_selector_keyboard(services, selected),
    )
    await callback.answer()


# Replace the legacy raw-ID prompt only for Rebecca. Marzban callbacks continue to
# fall through to the original sudo router because RebeccaOnly will not match.
@rebecca_services_router.callback_query(F.data.startswith("sales_renew_mode_"), RebeccaOnly())
async def rebecca_plan_create_services(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    mode = callback.data.split("_")[-1]
    if mode not in {"incremental", "reset"}:
        await callback.answer("حالت تمدید نامعتبر است.", show_alert=True)
        return
    await state.update_data(
        allow_incremental_renewal=(mode == "incremental"),
        rebecca_catalog_mode="create",
        rebecca_catalog_selected=[],
        rebecca_catalog_legacy_ids=[],
    )
    await state.set_state(CreatePlanStates.waiting_for_rebecca_services)
    await _render_plan_selector(callback, state)


@rebecca_services_router.callback_query(F.data.startswith("sales_edit_service_"), RebeccaOnly())
async def rebecca_plan_edit_services(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    try:
        plan_id = int(callback.data.rsplit("_", 1)[-1])
    except ValueError:
        await callback.answer("پلن نامعتبر است.", show_alert=True)
        return
    plan = await db.get_plan_by_id(plan_id)
    if not plan:
        await callback.answer("پلن پیدا نشد.", show_alert=True)
        return
    try:
        existing_ids = set(parse_service_ids(getattr(plan, "rebecca_service_ids", "") or ""))
    except ValueError:
        existing_ids = set()
    active = await list_services(enabled_only=True)
    selected = {item.id for item in active if item.rebecca_service_id in existing_ids}
    selected_provider_ids = {item.rebecca_service_id for item in active if item.id in selected}
    legacy_ids = sorted(existing_ids - selected_provider_ids)
    await state.update_data(
        edit_service_plan_id=plan_id,
        rebecca_catalog_mode="edit",
        rebecca_catalog_selected=sorted(selected),
        rebecca_catalog_legacy_ids=legacy_ids,
    )
    await state.set_state(EditPlanServicesStates.waiting_for_services)
    await _render_plan_selector(callback, state)


@rebecca_services_router.callback_query(F.data.startswith("rcp:t:"), RebeccaOnly())
async def rebecca_plan_toggle_service(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    internal_id = int(callback.data.rsplit(":", 1)[-1])
    service = await get_service(internal_id)
    if not service or not service.is_enabled:
        await callback.answer("این سرویس دیگر فعال نیست.", show_alert=True)
        return
    data = await state.get_data()
    selected = {int(item) for item in data.get("rebecca_catalog_selected", [])}
    if internal_id in selected:
        selected.remove(internal_id)
    else:
        selected.add(internal_id)
    await state.update_data(rebecca_catalog_selected=sorted(selected))
    await _render_plan_selector(callback, state)


@rebecca_services_router.callback_query(F.data == "rcp:manual", RebeccaOnly())
async def rebecca_plan_manual_fallback(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    data = await state.get_data()
    if data.get("rebecca_catalog_mode") == "edit":
        await state.set_state(EditPlanServicesStates.waiting_for_services)
    else:
        await state.set_state(CreatePlanStates.waiting_for_rebecca_services)
    await callback.message.edit_text(
        "🧰 حالت دستی/اضطراری\n\nService IDهای عددی Rebecca را با کاما وارد کن (مثال: 1,2)."
    )
    await callback.answer()


@rebecca_services_router.callback_query(F.data == "rcp:done", RebeccaOnly())
async def rebecca_plan_catalog_done(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    data = await state.get_data()
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

    if data.get("rebecca_catalog_mode") == "edit":
        plan_id = int(data["edit_service_plan_id"])
        ok = await db.update_plan(plan_id, rebecca_service_ids=canonical)
        await state.clear()
        await callback.message.edit_text(
            "✅ سرویس‌های پلن بروزرسانی شد." if ok else "❌ خطا در بروزرسانی پلن."
        )
        await callback.answer()
        return

    await state.update_data(rebecca_service_ids=canonical)
    await _save_sales_plan(callback, state)
