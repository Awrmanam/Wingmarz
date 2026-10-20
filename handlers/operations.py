from __future__ import annotations

from datetime import datetime, timezone
from html import escape
import math
import time

import aiosqlite
from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

import config
from checkout_presentation import plan_summary
from authorization import is_staff
from database import db
from operations_service import DiscountQuote, OperationsError, operations_service
from style_engine import style_engine
from group_ops_service import group_ops_service
from order_compliance import order_compliance_service
from trust_service import trust_service


operations_router = Router(name="operations")


MENU_SPECS = [
    ("ops:hub:panels", "پنل‌ها و ادمین‌ها", "panel", "👥"),
    ("ops:hub:sales", "فروش و سفارش‌ها", "sales", "🛒"),
    ("ops:hub:finance", "مالی و پرداخت‌ها", "finance", "💳"),
    ("ops:hub:customers", "کاربران و دسترسی‌ها", "users", "👤"),
    ("ops:hub:monitor", "گزارش و عملیات", "stats", "📊"),
    ("ops:hub:settings", "تنظیمات و ظاهر", "settings", "⚙️"),
]

QUICK_ACTIONS = (
    ("gops:admins:0", "مدیریت ادمین‌ها", "admin", "👥"),
    ("cc:orders:0", "سفارش‌های در انتظار", "orders", "🧾"),
)

HUB_MENUS = {
    "panels": {
        "title": "👥 پنل‌ها و ادمین‌ها",
        "body": "همه کارهای مربوط به پنل و ادمین از همین بخش انجام می‌شود.",
        "items": (
            ("gops:admins:0", "مدیریت ادمین‌ها", "admin", "👥"),
            ("add_admin", "افزودن ادمین", "admin", "➕"),
            ("import_admin", "واردکردن ادمین موجود", "admin", "⬇️"),
            ("auto_import_admins", "کشف ادمین‌های پنل", "search", "🔎"),
            ("rebecca_services", "سرویس‌های Rebecca", "plan", "🔌"),
            ("set_login_url", "آدرس ورود پنل", "settings", "🌐"),
        ),
    },
    "sales": {
        "title": "🛒 فروش و سفارش‌ها",
        "body": "فقط تنظیمات فروش: سفارش، محصول، تست و تخفیف.",
        "items": (
            ("cc:orders:0", "سفارش‌های در انتظار", "orders", "🧾"),
            ("sales_manage", "محصولات و پلن‌ها", "plan", "📦"),
            ("cc:test", "تست رایگان", "test", "🧪"),
            ("cc:discounts", "کدهای تخفیف", "discount", "🎟"),
        ),
    },
    "finance": {
        "title": "💳 مالی و پرداخت‌ها",
        "body": "رسیدها، کارت‌ها، صورتحساب و PAYG؛ بدون گزینه‌های فروش تکراری.",
        "items": (
            ("cc:orders:submitted:0", "رسیدهای منتظر تأیید", "finance", "🧾"),
            ("sales_cards", "کارت‌های بانکی", "finance", "💳"),
            ("set_billing", "تنظیمات صورتحساب", "settings", "🧮"),
            ("paygsvcadmin:home", "مدیریت PAYG", "finance", "⚡"),
        ),
    },
    "customers": {
        "title": "👤 کاربران و دسترسی‌ها",
        "body": "کاربران، KYC، پشتیبانی و دسترسی‌های مدیریتی در یکجا.",
        "items": (
            ("cc:users:0", "لیست کاربران", "users", "👤"),
            ("kycadmin:pending", "KYCهای در انتظار", "admin", "🪪"),
            ("cc:tickets:0", "تیکت‌های پشتیبانی", "support", "🎫"),
            ("cc:botadmins", "ادمین‌های ربات", "admin", "🧑‍💼"),
            ("forced_join_manage", "عضویت اجباری", "settings", "📢"),
            ("sudo_menu_broadcast", "ارسال پیام همگانی", "broadcast", "📣"),
        ),
    },
    "monitor": {
        "title": "📊 گزارش و عملیات",
        "body": "وضعیت سیستم، آمار و گروه عملیات.",
        "items": (
            ("cc:stats", "آمار و گزارشات", "stats", "📊"),
            ("admin_status", "وضعیت ادمین‌ها", "stats", "📡"),
            ("gops:home", "مرکز عملیات و گروه", "tools", "🧭"),
        ),
    },
    "settings": {
        "title": "⚙️ تنظیمات و ظاهر",
        "body": "نگهداری ربات، بکاپ و تنظیم ظاهر؛ بدون منوهای قدیمی.",
        "items": (
            ("sudo_menu_backup", "بکاپ و ریستور", "tools", "🗄"),
            ("sudo_menu_cleanup", "پاکسازی و نگهداری", "tools", "🧹"),
            ("style:menu", "ایموجی و استایل", "style", "✨"),
            ("cc:texts", "مدیریت متن‌ها", "text", "📝"),
            ("cc:buttons", "دکمه‌ها و منوها", "buttons", "🔘"),
        ),
    },
}


class DiscountStates(StatesGroup):
    code = State()
    value = State()
    min_order = State()
    max_uses = State()
    per_user = State()
    expiry_days = State()
    start_days = State()


class CheckoutDiscountStates(StatesGroup):
    code = State()


class BotAdminStates(StatesGroup):
    user_id = State()


class MenuEditStates(StatesGroup):
    label = State()


class TrialSettingStates(StatesGroup):
    traffic_gb = State()
    duration_minutes = State()
    cooldown_hours = State()


async def _button(text: str, callback_data: str, *, icon_key: str | None = None, fallback: str | None = None):
    return await style_engine.styled_button(
        text,
        callback_data=callback_data,
        icon_key=icon_key,
        fallback=fallback,
    )


def _sudo(user_id: int) -> bool:
    return is_staff(user_id)


async def _deny(callback: CallbackQuery) -> bool:
    if _sudo(callback.from_user.id):
        return False
    await callback.answer("غیرمجاز", show_alert=True)
    return True


async def _menu_label(callback_data: str, default: str) -> str:
    try:
        raw, visible = await style_engine.resolve_visual_alias("menu", callback_data)
        return default if visible == raw else visible
    except Exception:
        return default


async def build_operational_dashboard_keyboard() -> InlineKeyboardMarkup:
    """Compact two-level dashboard: daily shortcuts + eight logical sections."""
    rows = []

    quick = []
    for callback_data, default, icon_key, fallback in QUICK_ACTIONS:
        label = await _menu_label(callback_data, default)
        quick.append(await _button(label, callback_data, icon_key=icon_key, fallback=fallback))
    if quick:
        rows.append(quick)

    rendered = []
    for callback_data, default, icon_key, fallback in MENU_SPECS:
        visible = await operations_service.menu_is_visible(callback_data)
        if not visible:
            continue
        label = await _menu_label(callback_data, default)
        rendered.append(await _button(label, callback_data, icon_key=icon_key, fallback=fallback))

    for idx in range(0, len(rendered), 2):
        rows.append(rendered[idx:idx + 2])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _safe_count(conn: aiosqlite.Connection, sql: str, params=()) -> int:
    try:
        async with conn.execute(sql, params) as cur:
            row = await cur.fetchone()
        return int((row or [0])[0] or 0)
    except aiosqlite.OperationalError:
        return 0


async def _dashboard_text() -> str:
    async with aiosqlite.connect(config.DATABASE_PATH) as conn:
        pending_orders = await _safe_count(
            conn, "SELECT COUNT(*) FROM orders WHERE status IN ('pending','submitted')"
        )
        active_admins = await _safe_count(conn, "SELECT COUNT(*) FROM admins WHERE is_active=1")
        active_plans = await _safe_count(conn, "SELECT COUNT(*) FROM plans WHERE is_active=1")
        pending_kyc = await _safe_count(
            conn, "SELECT COUNT(*) FROM kyc_profiles WHERE status='pending'"
        )
        pending_payg = await _safe_count(
            conn, "SELECT COUNT(*) FROM payg_service_topups WHERE status='submitted'"
        ) + await _safe_count(
            conn, "SELECT COUNT(*) FROM payg_topups WHERE status='submitted'"
        )

    alerts = []
    if pending_orders:
        alerts.append(f"🧾 {pending_orders} سفارش در انتظار")
    if pending_kyc:
        alerts.append(f"🪪 {pending_kyc} KYC در انتظار")
    if pending_payg:
        alerts.append(f"💳 {pending_payg} شارژ PAYG در انتظار")

    alert_text = "\n".join(f"• {item}" for item in alerts) if alerts else "✅ مورد فوری برای بررسی ندارید."
    return (
        "🏠 <b>داشبورد مدیریت Wingmarz</b>\n\n"
        f"👥 پنل فعال: <b>{active_admins}</b>   •   📦 پلن فعال: <b>{active_plans}</b>\n\n"
        "<b>نیازمند توجه</b>\n"
        f"{alert_text}\n\n"
        "⚡ ردیف اول برای کارهای روزمره است؛ بقیه امکانات بر اساس موضوع دسته‌بندی شده‌اند."
    )


async def _render_hub(message: Message, key: str) -> None:
    # Old Telegram messages may still contain the previous hub callbacks.
    # Map them into the canonical IA instead of exposing an error/old menu.
    aliases = {
        "access": "customers",
        "users": "customers",
        "content": "settings",
    }
    key = aliases.get(str(key), str(key))
    spec = HUB_MENUS.get(key)
    if not spec:
        await message.edit_text(
            "❌ این بخش پیدا نشد.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                await _button("خانه", "back_to_main", icon_key="home", fallback="🏠")
            ]]),
        )
        return

    items = []
    for callback_data, title, icon_key, fallback in spec["items"]:
        items.append(
            await _button(title, callback_data, icon_key=icon_key, fallback=fallback)
        )

    rows = []
    for idx in range(0, len(items), 2):
        rows.append(items[idx:idx + 2])
    rows.append([await _button("🏠 بازگشت به داشبورد", "back_to_main", icon_key="home", fallback="🏠")])

    await message.edit_text(
        f"<b>{spec['title']}</b>\n\n{spec['body']}\n\nگزینه موردنظر را انتخاب کنید:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


async def _send_dashboard(message: Message) -> None:
    await operations_service.ensure_schema()
    await message.answer(await _dashboard_text(), reply_markup=await build_operational_dashboard_keyboard())


async def _edit_dashboard(message: Message) -> None:
    await operations_service.ensure_schema()
    await message.edit_text(await _dashboard_text(), reply_markup=await build_operational_dashboard_keyboard())


@operations_router.message(CommandStart(), F.from_user.id.in_(config.SUDO_ADMINS))
@operations_router.message(Command("dashboard"), F.from_user.id.in_(config.SUDO_ADMINS))
async def operational_start(message: Message, state: FSMContext):
    await state.clear()
    await _send_dashboard(message)


@operations_router.callback_query(F.data == "back_to_main")
async def operational_back(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    await state.clear()
    await _edit_dashboard(callback.message)
    await callback.answer()


@operations_router.callback_query(F.data.startswith("ops:hub:"))
async def operational_hub(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    await state.clear()
    key = (callback.data or "").rsplit(":", 1)[-1]
    await _render_hub(callback.message, key)
    await callback.answer()


# ---------------------------- Discounts ----------------------------

async def _render_discounts(message: Message) -> None:
    items = await operations_service.list_discounts()
    lines = ["🎟 <b>مدیریت تخفیف‌ها</b>", ""]
    rows = []
    if not items:
        lines.append("هنوز کد تخفیفی ساخته نشده است.")
    for item in items:
        state = "✅" if item["is_active"] else "⛔"
        kind = f"{item['value']}٪" if item["kind"] == "percent" else f"{int(item['value']):,} تومان"
        max_txt = "∞" if int(item["max_uses"] or 0) == 0 else str(item["max_uses"])
        lines.append(
            f"{state} <code>{escape(item['code'])}</code> · {kind} · استفاده {item['used_count']}/{max_txt}"
        )
        rows.append([await _button(f"{state} {item['code']}", f"ops:disc:item:{item['id']}", fallback="🎟")])
    rows.append([await _button("ساخت کد تخفیف", "ops:disc:add", fallback="➕")])
    rows.append([await _button("خانه", "back_to_main", icon_key="home", fallback="🏠")])
    await message.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@operations_router.callback_query(F.data == "cc:discounts")
async def discounts_menu(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    await state.clear()
    await _render_discounts(callback.message)
    await callback.answer()


@operations_router.callback_query(F.data == "ops:disc:add")
async def discount_add(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    await state.clear()
    await state.set_state(DiscountStates.code)
    await callback.message.edit_text(
        "🎟 کد تخفیف را ارسال کنید.\nنمونه: <code>RETURN10</code>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[await _button("لغو", "cc:discounts", fallback="❌")]]),
    )
    await callback.answer()


@operations_router.message(DiscountStates.code, F.text)
async def discount_code_input(message: Message, state: FSMContext):
    if not _sudo(message.from_user.id):
        return
    try:
        code = operations_service.normalize_discount_code(message.text)
    except OperationsError as exc:
        await message.answer(f"❌ {escape(str(exc))}")
        return
    await state.update_data(discount_code=code)
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        await _button("درصدی", "ops:disc:type:percent", fallback="%"),
        await _button("مبلغ ثابت", "ops:disc:type:fixed", fallback="💵"),
    ]])
    await message.answer("نوع تخفیف را انتخاب کنید:", reply_markup=kb)


@operations_router.callback_query(F.data.startswith("ops:disc:type:"), DiscountStates.code)
async def discount_type(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    kind = (callback.data or "").rsplit(":", 1)[-1]
    if kind not in {"percent", "fixed"}:
        await callback.answer("نامعتبر", show_alert=True)
        return
    await state.update_data(discount_kind=kind)
    await state.set_state(DiscountStates.value)
    await callback.message.answer("مقدار تخفیف را عددی ارسال کنید:")
    await callback.answer()


@operations_router.message(DiscountStates.value, F.text)
async def discount_value_input(message: Message, state: FSMContext):
    if not _sudo(message.from_user.id):
        return
    try:
        value = int((message.text or "").replace(",", "").strip())
    except ValueError:
        await message.answer("❌ فقط عدد وارد کنید.")
        return
    data = await state.get_data()
    if data.get("discount_kind") == "percent" and not (1 <= value <= 100):
        await message.answer("❌ درصد باید بین ۱ تا ۱۰۰ باشد.")
        return
    if data.get("discount_kind") == "fixed" and value <= 0:
        await message.answer("❌ مبلغ باید بیشتر از صفر باشد.")
        return
    await state.update_data(discount_value=value)
    await state.set_state(DiscountStates.min_order)
    await message.answer("حداقل مبلغ سفارش را به تومان بفرستید؛ برای بدون حداقل <code>0</code> بفرستید.")


@operations_router.message(DiscountStates.min_order, F.text)
async def discount_min_input(message: Message, state: FSMContext):
    if not _sudo(message.from_user.id):
        return
    try:
        value = max(0, int((message.text or "").replace(",", "").strip()))
    except ValueError:
        await message.answer("❌ عدد معتبر وارد کنید.")
        return
    await state.update_data(discount_min_order=value)
    await state.set_state(DiscountStates.max_uses)
    await message.answer("حداکثر تعداد استفاده کل را بفرستید؛ <code>0</code> یعنی نامحدود.")


@operations_router.message(DiscountStates.max_uses, F.text)
async def discount_max_input(message: Message, state: FSMContext):
    if not _sudo(message.from_user.id):
        return
    try:
        value = max(0, int((message.text or "").strip()))
    except ValueError:
        await message.answer("❌ عدد معتبر وارد کنید.")
        return
    await state.update_data(discount_max_uses=value)
    await state.set_state(DiscountStates.per_user)
    await message.answer("حداکثر استفاده هر کاربر را بفرستید. مثال: <code>1</code>")


@operations_router.message(DiscountStates.per_user, F.text)
async def discount_per_user_input(message: Message, state: FSMContext):
    if not _sudo(message.from_user.id):
        return
    try:
        value = int((message.text or "").strip())
    except ValueError:
        await message.answer("❌ عدد معتبر وارد کنید.")
        return
    if value < 1:
        await message.answer("❌ حداقل ۱ بار.")
        return
    await state.update_data(discount_per_user=value)
    await state.set_state(DiscountStates.expiry_days)
    await message.answer("اعتبار کد چند روز باشد؟ <code>0</code> یعنی بدون تاریخ انقضا.")


@operations_router.message(DiscountStates.expiry_days, F.text)
async def discount_expiry_input(message: Message, state: FSMContext):
    if not _sudo(message.from_user.id):
        return
    try:
        days = max(0, int((message.text or "").strip()))
    except ValueError:
        await message.answer("❌ عدد معتبر وارد کنید.")
        return
    await state.update_data(discount_expiry_days=days)
    await state.set_state(DiscountStates.start_days)
    await message.answer("شروع کد چند روز دیگر باشد؟ صفر یعنی همین حالا.")


@operations_router.message(DiscountStates.start_days, F.text)
async def discount_start_input(message, state):
    if not _sudo(message.from_user.id):
        return
    try:
        delay = int(message.text.strip())
        if delay < 0 or delay > 3650:
            raise ValueError
    except (ValueError, AttributeError):
        await message.answer("عدد بین صفر تا ۳۶۵۰ وارد کنید.")
        return
    data = await state.get_data()
    days = int(data.get("discount_expiry_days") or 0)
    starts_at = int(time.time()) + delay * 86400
    expires_at = starts_at + days * 86400 if days else None
    try:
        discount_id = await operations_service.create_discount(
            code=data["discount_code"],
            kind=data["discount_kind"],
            value=int(data["discount_value"]),
            min_order=int(data["discount_min_order"]),
            max_uses=int(data["discount_max_uses"]),
            per_user_limit=int(data["discount_per_user"]),
            expires_at=expires_at,
            starts_at=starts_at,
            created_by=message.from_user.id,
        )
    except (OperationsError, KeyError) as exc:
        await state.clear()
        await message.answer(f"❌ {escape(str(exc))}")
        return
    await state.clear()
    await message.answer("✅ کد تخفیف ساخته شد.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[await _button("مدیریت تخفیف‌ها", "cc:discounts")]]))


@operations_router.callback_query(F.data.startswith("ops:disc:item:"))
async def discount_detail(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    await state.clear()
    try:
        item_id = int((callback.data or "").rsplit(":", 1)[-1])
    except ValueError:
        await callback.answer("نامعتبر", show_alert=True)
        return
    item = await operations_service.get_discount(item_id)
    if not item:
        await callback.answer("پیدا نشد", show_alert=True)
        return
    kind = f"{item['value']}٪" if item["kind"] == "percent" else f"{int(item['value']):,} تومان"
    expires = operations_service.format_timestamp(item["expires_at"])
    text = (
        f"🎟 <b>{escape(item['code'])}</b>\n\n"
        f"نوع/مقدار: <b>{kind}</b>\n"
        f"حداقل سفارش: <b>{int(item['min_order'] or 0):,}</b>\n"
        f"سقف کل: <b>{'نامحدود' if not item['max_uses'] else item['max_uses']}</b>\n"
        f"سقف هر کاربر: <b>{item['per_user_limit']}</b>\n"
        f"شروع: <code>{escape(operations_service.format_timestamp(item.get('starts_at')))}</code>\n"
        f"انقضا: <code>{escape(expires)}</code>\n"
        f"وضعیت: {'✅ فعال' if item['is_active'] else '⛔ غیرفعال'}"
    )
    rows = [
        [await _button(
            "غیرفعال کردن" if item["is_active"] else "فعال کردن",
            f"ops:disc:toggle:{item_id}",
            fallback="🔁",
        )],
        [await _button("حذف", f"ops:disc:delete:{item_id}", fallback="🗑")],
        [await _button("بازگشت", "cc:discounts", icon_key="back", fallback="⬅️")],
    ]
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


@operations_router.callback_query(F.data.startswith("ops:disc:toggle:"))
async def discount_toggle(callback: CallbackQuery):
    if await _deny(callback):
        return
    item_id = int((callback.data or "").rsplit(":", 1)[-1])
    item = await operations_service.get_discount(item_id)
    if not item:
        await callback.answer("پیدا نشد", show_alert=True)
        return
    await operations_service.set_discount_active(item_id, not bool(item["is_active"]))
    await _render_discounts(callback.message)
    await callback.answer("ذخیره شد")


@operations_router.callback_query(F.data.startswith("ops:disc:delete:"))
async def discount_delete(callback: CallbackQuery):
    if await _deny(callback):
        return
    item_id = int((callback.data or "").rsplit(":", 1)[-1])
    await operations_service.delete_discount(item_id)
    await _render_discounts(callback.message)
    await callback.answer("حذف/غیرفعال شد")


async def _checkout_prompt(callback: CallbackQuery, source: str, plan_id: int, state: FSMContext) -> None:
    plans = await db.get_plans(only_active=True)
    plan = next((p for p in plans if p.id == plan_id), None)
    if not plan:
        await callback.answer("پلن یافت نشد.", show_alert=True)
        return
    if not await operations_service.has_active_discounts(int(plan.price)):
        await _create_checkout_order(callback, source, plan_id, None)
        return
    await state.clear()
    rows = [
        [await _button("اعمال کد تخفیف", f"ops:checkout:code:{source}:{plan_id}", fallback="🎟")],
        [await _button("ادامه بدون تخفیف", f"ops:checkout:nocode:{source}:{plan_id}", fallback="➡️")],
        [await _button("بازگشت", "admin_buy_reseller" if source == "a" else "public_buy_reseller")],
    ]
    await callback.message.edit_text(
        plan_summary(plan) + "\n\n" + config.MESSAGES["sales_discount_prompt"],
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@operations_router.callback_query(F.data.startswith("public_order_"))
async def public_checkout_intercept(callback: CallbackQuery, state: FSMContext):
    try:
        plan_id = int((callback.data or "").split("_")[-1])
    except ValueError:
        await callback.answer("پلن نامعتبر", show_alert=True)
        return
    await _checkout_prompt(callback, "p", plan_id, state)


@operations_router.callback_query(F.data.startswith("admin_order_"))
async def admin_checkout_intercept(callback: CallbackQuery, state: FSMContext):
    try:
        plan_id = int((callback.data or "").split("_")[-1])
    except ValueError:
        await callback.answer("پلن نامعتبر", show_alert=True)
        return
    await _checkout_prompt(callback, "a", plan_id, state)


@operations_router.callback_query(F.data.startswith("ops:checkout:nocode:"))
async def checkout_no_code(callback: CallbackQuery, state: FSMContext):
    parts = (callback.data or "").split(":")
    if len(parts) != 5 or parts[3] not in {"a", "p"} or not parts[4].isdigit() or int(parts[4]) <= 0:
        await callback.answer("نامعتبر", show_alert=True)
        return
    source, plan_id_raw = parts[3], parts[4]
    await state.clear()
    await _create_checkout_order(callback, source, int(plan_id_raw), None)


@operations_router.callback_query(F.data.startswith("ops:checkout:code:"))
async def checkout_code_start(callback: CallbackQuery, state: FSMContext):
    parts = (callback.data or "").split(":")
    if len(parts) != 5 or parts[3] not in {"a", "p"} or not parts[4].isdigit() or int(parts[4]) <= 0:
        await callback.answer("نامعتبر", show_alert=True)
        return
    source, plan_id_raw = parts[3], parts[4]
    await state.clear()
    await state.update_data(checkout_source=source, checkout_plan_id=int(plan_id_raw))
    await state.set_state(CheckoutDiscountStates.code)
    await callback.message.edit_text("🎟 کد تخفیف را ارسال کنید:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [await _button("ادامه بدون تخفیف", f"ops:checkout:nocode:{source}:{plan_id_raw}")],
        [await _button("بازگشت", "admin_buy_reseller" if source == "a" else "public_buy_reseller")]]))
    await callback.answer()


@operations_router.message(CheckoutDiscountStates.code, F.text)
async def checkout_code_value(message: Message, state: FSMContext):
    data = await state.get_data()
    plan_id = int(data.get("checkout_plan_id") or 0)
    source = str(data.get("checkout_source") or "p")
    plan = await db.get_plan_by_id(plan_id)
    if not plan or not getattr(plan, "is_active", True):
        await state.clear()
        await message.answer("❌ پلن یافت نشد.")
        return
    try:
        quote = await operations_service.quote_discount(message.text or "", message.from_user.id, int(plan.price))
    except OperationsError as exc:
        await message.answer(f"❌ {escape(str(exc))}", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [await _button("ادامه بدون تخفیف", f"ops:checkout:nocode:{source}:{plan_id}")],
            [await _button("بازگشت", "admin_buy_reseller" if source == "a" else "public_buy_reseller")]]))
        return
    await state.clear()
    fake_callback = _MessageCheckoutAdapter(message)
    await _create_checkout_order(fake_callback, source, plan_id, quote)


class _CheckoutMessage:
    def __init__(self, message):
        self._message = message

    async def edit_text(self, text, **kwargs):
        return await self._message.answer(text, **kwargs)

    async def answer(self, text, **kwargs):
        return await self._message.answer(text, **kwargs)


class _MessageCheckoutAdapter:
    """Tiny adapter so one checkout renderer can answer from callback or FSM message."""
    def __init__(self, message: Message):
        self.message = _CheckoutMessage(message)
        self.from_user = message.from_user
        self.bot = message.bot

    async def answer(self, *_args, **_kwargs):
        return None


async def _notify_free_order(bot, order_id: int, user_id: int, plan_name: str, final_price: int, code: str | None) -> None:
    rows = [[
        await _button("تایید و صدور", f"order_approve_{order_id}", fallback="✅"),
        await _button("رد", f"order_reject_{order_id}", fallback="❌"),
    ]]
    suffix = f"\n🎟 کد: <code>{escape(code)}</code>" if code else ""
    try:
        await group_ops_service.send_text(
            bot,
            "orders",
            f"🧾 <b>سفارش رایگان آماده تأیید #{order_id}</b>\n"
            f"👤 <code>{user_id}</code>\n📦 {escape(plan_name)}\n💵 {final_price:,} تومان{suffix}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )
    except Exception:
        pass
    for sudo_id in config.SUDO_ADMINS:
        try:
            await bot.send_message(
                int(sudo_id),
                f"🧾 <b>سفارش بدون نیاز به پرداخت #{order_id}</b>\n"
                f"👤 <code>{user_id}</code>\n📦 {escape(plan_name)}\n💵 {final_price:,} تومان{suffix}",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
            )
        except Exception:
            pass


async def _create_checkout_order(callback: CallbackQuery | _MessageCheckoutAdapter, source: str, plan_id: int, quote: DiscountQuote | None) -> None:
    plan = await db.get_plan_by_id(int(plan_id))
    if not plan or not getattr(plan, "is_active", True):
        await callback.answer("پلن یافت نشد.", show_alert=True)
        return
    original_price = int(plan.price)
    final_price = quote.final_price if quote else original_price
    order_id = await db.add_order(callback.from_user.id, int(plan_id), final_price, plan.name)
    if not order_id:
        await callback.answer("خطا در ثبت سفارش.", show_alert=True)
        return

    identity_approved = await trust_service.is_identity_approved(callback.from_user.id)
    gate = await order_compliance_service.ensure_order(
        order_id,
        callback.from_user.id,
        kyc_required=not identity_approved,
        payment_approved=final_price <= 0,
    )
    try:
        if await group_ops_service.claim_event(f"order:{order_id}:created"):
            await group_ops_service.send_text(
                callback.bot,
                "orders",
                "🛒 <b>سفارش جدید</b>\n\n"
                f"🧾 سفارش: <code>#{order_id}</code>\n"
                f"👤 User ID: <code>{callback.from_user.id}</code>\n"
                f"🔗 Username: {('@' + escape(callback.from_user.username)) if callback.from_user.username else '—'}\n"
                f"📦 پلن: <b>{escape(plan.name)}</b>\n"
                f"💵 مبلغ: <b>{final_price:,}</b> تومان",
            )
    except Exception:
        pass
    if quote:
        try:
            await operations_service.record_redemption(quote, callback.from_user.id, order_id)
            await db.update_order(
                order_id,
                payment_note=f"discount={quote.code};original={quote.original_price};discount={quote.discount_amount}",
            )
        except Exception:
            await db.update_order(order_id, status="cancelled")
            await callback.message.answer("❌ رزرو کد تخفیف ناموفق بود؛ سفارش لغو شد.")
            return

    discount_lines = ""
    if quote:
        discount_lines = (
            f"\n🎟 کد: <code>{escape(quote.code)}</code>"
            f"\nقیمت اولیه: <s>{quote.original_price:,}</s>"
            f"\nتخفیف: {quote.discount_amount:,} تومان"
        )

    if final_price <= 0:
        await db.update_order(order_id, status="submitted")
        if gate.ready_to_issue:
            await callback.message.edit_text(
                f"✅ سفارش #{order_id} ثبت شد.\n📦 {escape(plan.name)}{discount_lines}\n"
                "💵 مبلغ نهایی: <b>0 تومان</b>\n\nبرای تایید و صدور به مدیریت ارسال شد."
            )
            await _notify_free_order(
                callback.bot,
                order_id,
                callback.from_user.id,
                plan.name,
                final_price,
                quote.code if quote else None,
            )
        else:
            await callback.message.edit_text(
                f"✅ سفارش #{order_id} ثبت شد.\n📦 {escape(plan.name)}{discount_lines}\n"
                "💵 مبلغ نهایی: <b>0 تومان</b>\n\n"
                "🪪 برای فعال‌سازی این سفارش باید احراز هویت را تکمیل کنید.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    await _button("🪪 شروع احراز هویت", "kyc:start", fallback="🪪")
                ]]),
            )
            await group_ops_service.send_text(
                callback.bot,
                "orders",
                f"🟡 <b>سفارش رایگان #{order_id} منتظر KYC است.</b>\n"
                f"👤 <code>{callback.from_user.id}</code>\n📦 {escape(plan.name)}",
            )
        await callback.answer()
        return

    cards = await db.get_cards(only_active=True)
    lines = [
        f"✅ سفارش <b>#{order_id}</b> ثبت شد.\n\n"
        f"پلن: {escape(plan.name)}{discount_lines}\n"
        f"قیمت نهایی: <b>{final_price:,} تومان</b>\n",
    ]
    if gate.kyc_required:
        lines.extend([
            "🪪 <b>احراز هویت برای فعال‌سازی این سفارش الزامی است.</b>",
            "ابتدا پرداخت و رسید را ثبت کنید؛ بعد از تأیید پرداخت، بات از شما KYC می‌خواهد.",
            "اگر KYC تأیید نشود، سفارش وارد صف استرداد وجه می‌شود.",
            "",
        ])
    else:
        lines.extend([
            "✅ هویت این حساب قبلاً تأیید شده و KYC مجدد لازم نیست.",
            "",
        ])
    lines.extend([
        config.MESSAGES["public_payment_instructions"],
        "",
        "کارت‌های فعال:",
    ])
    if not cards:
        lines.append("— فعلاً کارتی ثبت نشده. لطفاً با پشتیبانی تماس بگیرید.")
    else:
        for card in cards:
            lines.append(
                f"• {escape(str(card.get('bank_name','بانک')))} | "
                f"<code>{escape(str(card.get('card_number','---- ---- ---- ----')))}</code> | "
                f"{escape(str(card.get('holder_name','')))}"
            )
    back_cb = "back_to_admin_main" if source == "a" else "public_back_main"
    paid_cb = f"admin_mark_paid_{order_id}" if source == "a" else f"public_mark_paid_{order_id}"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [await _button(config.BUTTONS["mark_paid"], paid_cb, fallback="✅")],
        [await _button("بازگشت", back_cb, icon_key="back", fallback="⬅️")],
    ])
    await callback.message.edit_text("\n".join(lines), reply_markup=kb)
    await callback.answer()


# ---------------------------- Trial configs ----------------------------

async def _render_trial_settings(message: Message) -> None:
    settings = await operations_service.get_trial_settings()
    traffic_gb = settings["traffic_bytes"] / (1024 ** 3)
    duration_min = settings["duration_seconds"] // 60
    cooldown_h = settings["cooldown_seconds"] / 3600
    service = settings["rebecca_service_id"] or "خودکار/اولین سرویس فعال"
    text = (
        "🧪 <b>کانفیگ تست</b>\n\n"
        f"وضعیت: {'✅ فعال' if settings['enabled'] else '⛔ غیرفعال'}\n"
        f"حجم: <b>{traffic_gb:g} GB</b>\n"
        f"مدت: <b>{duration_min} دقیقه</b>\n"
        f"Cooldown هر کاربر: <b>{cooldown_h:g} ساعت</b>\n"
        f"Provider: <code>{escape(str(config.PANEL_PROVIDER))}</code>\n"
    )
    if config.PANEL_PROVIDER == "rebecca":
        text += f"Service ID: <code>{escape(str(service))}</code>\n"
    text += "\nکاربر با دستور /test کانفیگ را دریافت می‌کند."
    rows = [
        [await _button("خاموش کردن" if settings["enabled"] else "فعال کردن", "ops:trial:toggle", fallback="🔁")],
        [
            await _button("تنظیم حجم", "ops:trial:traffic", fallback="📦"),
            await _button("تنظیم مدت", "ops:trial:duration", fallback="⏱"),
        ],
        [await _button("تنظیم فاصله دریافت", "ops:trial:cooldown", fallback="🕒")],
    ]
    if config.PANEL_PROVIDER == "rebecca":
        rows.append([await _button("انتخاب سرویس Rebecca", "ops:trial:services", fallback="🔌")])
    rows.extend([
        [await _button("صدور تست برای خودم", "ops:trial:self", fallback="🧪")],
        [await _button("خانه", "back_to_main", icon_key="home", fallback="🏠")],
    ])
    await message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


async def trial_menu(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    await state.clear()
    await _render_trial_settings(callback.message)
    await callback.answer()


@operations_router.callback_query(F.data == "ops:trial:toggle")
async def trial_toggle(callback: CallbackQuery):
    if await _deny(callback):
        return
    settings = await operations_service.get_trial_settings()
    await operations_service.set_trial_setting("enabled", not settings["enabled"])
    await _render_trial_settings(callback.message)
    await callback.answer("ذخیره شد")


@operations_router.callback_query(F.data == "ops:trial:traffic")
async def trial_traffic_start(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    await state.clear()
    await state.set_state(TrialSettingStates.traffic_gb)
    await callback.message.answer("حجم تست را به GB وارد کنید. مثال: <code>1</code> یا <code>0.5</code>")
    await callback.answer()


@operations_router.message(TrialSettingStates.traffic_gb, F.text)
async def trial_traffic_value(message: Message, state: FSMContext):
    if not _sudo(message.from_user.id):
        return
    try:
        gb = float((message.text or "").replace(",", ".").strip())
    except ValueError:
        await message.answer("❌ عدد معتبر وارد کنید.")
        return
    if not (0.05 <= gb <= 100):
        await message.answer("❌ حجم باید بین 0.05 تا 100 GB باشد.")
        return
    await operations_service.set_trial_setting("traffic_bytes", int(gb * 1024 ** 3))
    await state.clear()
    await message.answer("✅ حجم تست ذخیره شد.")


@operations_router.callback_query(F.data == "ops:trial:duration")
async def trial_duration_start(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    await state.clear()
    await state.set_state(TrialSettingStates.duration_minutes)
    await callback.message.answer("مدت تست را به دقیقه وارد کنید. مثال: <code>60</code>")
    await callback.answer()


@operations_router.message(TrialSettingStates.duration_minutes, F.text)
async def trial_duration_value(message: Message, state: FSMContext):
    if not _sudo(message.from_user.id):
        return
    try:
        minutes = int((message.text or "").strip())
    except ValueError:
        await message.answer("❌ عدد معتبر وارد کنید.")
        return
    if not (5 <= minutes <= 10080):
        await message.answer("❌ مدت باید بین ۵ دقیقه تا ۷ روز باشد.")
        return
    await operations_service.set_trial_setting("duration_seconds", minutes * 60)
    await state.clear()
    await message.answer("✅ مدت تست ذخیره شد.")


@operations_router.callback_query(F.data == "ops:trial:cooldown")
async def trial_cooldown_start(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    await state.clear()
    await state.set_state(TrialSettingStates.cooldown_hours)
    await callback.message.answer("فاصله دریافت مجدد را به ساعت بفرستید؛ <code>0</code> یعنی بدون محدودیت.")
    await callback.answer()


@operations_router.message(TrialSettingStates.cooldown_hours, F.text)
async def trial_cooldown_value(message: Message, state: FSMContext):
    if not _sudo(message.from_user.id):
        return
    try:
        hours = float((message.text or "").replace(",", ".").strip())
    except ValueError:
        await message.answer("❌ عدد معتبر وارد کنید.")
        return
    if not (0 <= hours <= 720):
        await message.answer("❌ مقدار باید بین ۰ تا ۷۲۰ ساعت باشد.")
        return
    await operations_service.set_trial_setting("cooldown_seconds", int(hours * 3600))
    await state.clear()
    await message.answer("✅ فاصله دریافت ذخیره شد.")


@operations_router.callback_query(F.data == "ops:trial:services")
async def trial_services(callback: CallbackQuery):
    if await _deny(callback):
        return
    if config.PANEL_PROVIDER != "rebecca":
        await callback.answer("فقط Rebecca", show_alert=True)
        return
    async with aiosqlite.connect(config.DATABASE_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        try:
            async with conn.execute(
                "SELECT id,service_id,display_name,service_name FROM rebecca_services WHERE is_active=1 ORDER BY id"
            ) as cur:
                items = await cur.fetchall()
        except aiosqlite.OperationalError:
            items = []
    rows = []
    for item in items:
        name = item["display_name"] or item["service_name"] or f"Service {item['service_id']}"
        rows.append([await _button(str(name), f"ops:trial:service:{item['service_id']}", fallback="🔌")])
    rows.append([await _button("انتخاب خودکار", "ops:trial:service:0", fallback="♻️")])
    rows.append([await _button("بازگشت", "cc:test", icon_key="back", fallback="⬅️")])
    await callback.message.edit_text(
        "🔌 سرویس موردنظر برای کانفیگ تست را انتخاب کنید:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@operations_router.callback_query(F.data.startswith("ops:trial:service:"))
async def trial_service_select(callback: CallbackQuery):
    if await _deny(callback):
        return
    try:
        service_id = int((callback.data or "").rsplit(":", 1)[-1])
    except ValueError:
        await callback.answer("نامعتبر", show_alert=True)
        return
    await operations_service.set_trial_setting("rebecca_service_id", service_id if service_id > 0 else None)
    await _render_trial_settings(callback.message)
    await callback.answer("ذخیره شد")


async def _send_trial_result(message: Message, target_user_id: int) -> None:
    try:
        result = await operations_service.issue_trial(target_user_id)
    except OperationsError as exc:
        await message.answer(f"❌ {escape(str(exc))}")
        return
    from handlers.trial_ui_v2 import render_config_result
    await render_config_result(message, target_user_id, result)


async def public_trial_command(message: Message, state: FSMContext):
    await state.clear()
    await _send_trial_result(message, message.from_user.id)


@operations_router.callback_query(F.data == "ops:trial:self")
async def trial_self(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    await state.clear()
    await _send_trial_result(callback.message, callback.from_user.id)
    await callback.answer()


# ---------------------------- Runtime SUDO admins ----------------------------

async def _render_bot_admins(message: Message) -> None:
    dynamic = await operations_service.list_runtime_admins()
    lines = ["🧑‍💼 <b>ادمین‌های ربات</b>", "", "SUDOهای اصلی سرور:"]
    for user_id in operations_service.base_sudo_ids:
        lines.append(f"🔒 <code>{user_id}</code>")
    lines.append("")
    lines.append("ادمین‌های اضافه‌شده از داخل ربات:")
    rows = []
    if not dynamic:
        lines.append("— موردی ثبت نشده")
    for item in dynamic:
        state = "✅" if item["is_active"] else "⛔"
        lines.append(f"{state} <code>{item['user_id']}</code>")
        rows.append([await _button(f"{state} {item['user_id']}", f"ops:ba:item:{item['user_id']}", fallback="👤")])
    rows.extend([
        [await _button("افزودن مدیر", "ops:ba:add", fallback="➕")],
        [await _button("خانه", "back_to_main", icon_key="home", fallback="🏠")],
    ])
    lines.extend(["", "مدیر به بخش‌های عملیاتی دسترسی دارد؛ تغییر دسترسی مدیران فقط با مالک است."])
    await message.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@operations_router.callback_query(F.data == "cc:botadmins")
async def bot_admins_menu(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in operations_service.base_sudo_ids:
        await callback.answer("فقط مالک ربات اجازه مدیریت دسترسی‌ها را دارد.", show_alert=True)
        return
    await state.clear()
    await _render_bot_admins(callback.message)
    await callback.answer()


@operations_router.callback_query(F.data == "ops:ba:add")
async def bot_admin_add_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in operations_service.base_sudo_ids:
        await callback.answer("فقط مالک ربات اجازه مدیریت دسترسی‌ها را دارد.", show_alert=True)
        return
    await state.clear()
    await state.set_state(BotAdminStates.user_id)
    await callback.message.answer("User ID عددی ادمین جدید را ارسال کنید:")
    await callback.answer()


@operations_router.message(BotAdminStates.user_id, F.text)
async def bot_admin_add_value(message: Message, state: FSMContext):
    if message.from_user.id not in operations_service.base_sudo_ids:
        return
    try:
        user_id = int((message.text or "").strip())
        await operations_service.add_runtime_admin(user_id, message.from_user.id)
    except (ValueError, OperationsError) as exc:
        await message.answer(f"❌ {escape(str(exc))}")
        return
    await state.clear()
    await message.answer(f"✅ <code>{user_id}</code> به SUDOهای ربات اضافه شد.")


@operations_router.callback_query(F.data.startswith("ops:ba:item:"))
async def bot_admin_detail(callback: CallbackQuery):
    if callback.from_user.id not in operations_service.base_sudo_ids:
        await callback.answer("فقط مالک ربات اجازه مدیریت دسترسی‌ها را دارد.", show_alert=True)
        return
    user_id = int((callback.data or "").rsplit(":", 1)[-1])
    item = next((x for x in await operations_service.list_runtime_admins() if int(x["user_id"]) == user_id), None)
    if not item:
        await callback.answer("پیدا نشد", show_alert=True)
        return
    rows = [
        [await _button("غیرفعال کردن" if item["is_active"] else "فعال کردن", f"ops:ba:toggle:{user_id}", fallback="🔁")],
        [await _button("حذف", f"ops:ba:delete:{user_id}", fallback="🗑")],
        [await _button("بازگشت", "cc:botadmins", icon_key="back", fallback="⬅️")],
    ]
    await callback.message.edit_text(
        f"🧑‍💼 <b>ادمین ربات</b>\n\nUser ID: <code>{user_id}</code>\nوضعیت: {'✅ فعال' if item['is_active'] else '⛔ غیرفعال'}\nافزوده توسط: <code>{escape(str(item['added_by'] or '-'))}</code>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@operations_router.callback_query(F.data.startswith("ops:ba:toggle:"))
async def bot_admin_toggle(callback: CallbackQuery):
    if callback.from_user.id not in operations_service.base_sudo_ids:
        await callback.answer("فقط مالک ربات اجازه مدیریت دسترسی‌ها را دارد.", show_alert=True)
        return
    user_id = int((callback.data or "").rsplit(":", 1)[-1])
    item = next((x for x in await operations_service.list_runtime_admins() if int(x["user_id"]) == user_id), None)
    if not item:
        await callback.answer("پیدا نشد", show_alert=True)
        return
    try:
        await operations_service.set_runtime_admin_active(user_id, not bool(item["is_active"]), actor_id=callback.from_user.id)
    except OperationsError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await _render_bot_admins(callback.message)
    await callback.answer("ذخیره شد")


@operations_router.callback_query(F.data.startswith("ops:ba:delete:"))
async def bot_admin_delete(callback: CallbackQuery):
    if callback.from_user.id not in operations_service.base_sudo_ids:
        await callback.answer("فقط مالک ربات اجازه مدیریت دسترسی‌ها را دارد.", show_alert=True)
        return
    user_id = int((callback.data or "").rsplit(":", 1)[-1])
    try:
        await operations_service.remove_runtime_admin(user_id, actor_id=callback.from_user.id)
    except OperationsError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await _render_bot_admins(callback.message)
    await callback.answer("حذف شد")


# ---------------------------- Text / button management ----------------------------

async def texts_menu(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    await state.clear()
    items = await style_engine.list_overrides()
    rows = [
        [await _button("مدیریت Text Overrideها", "style:aliases", fallback="📝")],
        [await _button("مدیریت دکمه‌های داشبورد", "cc:buttons", fallback="🔘")],
        [await _button("خانه", "back_to_main", icon_key="home", fallback="🏠")],
    ]
    await callback.message.edit_text(
        "📝 <b>مدیریت متن‌ها</b>\n\n"
        f"Overrideهای ثبت‌شده: <b>{len(items)}</b>\n"
        "برای متن‌های نمایشی از Text Override استفاده می‌شود؛ شناسه‌های فنی تغییر نمی‌کنند.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


async def _render_buttons_menu(message: Message) -> None:
    lines = ["🔘 <b>دکمه‌ها و منوهای داشبورد</b>", "", "روی هر مورد بزنید تا نام یا نمایش/عدم نمایش آن را تغییر دهید."]
    rows = []
    seen_callbacks = set()
    for idx, (callback_data, default, _icon, _fallback) in enumerate(MENU_SPECS):
        # Keep one editor row per callback identity.
        if callback_data in seen_callbacks:
            continue
        seen_callbacks.add(callback_data)
        visible = await operations_service.menu_is_visible(callback_data)
        label = await _menu_label(callback_data, default)
        state = "✅" if visible else "⛔"
        rows.append([await _button(f"{state} {label}", f"ops:menu:item:{idx}", fallback="🔘")])
    rows.append([await _button("خانه", "back_to_main", icon_key="home", fallback="🏠")])
    await message.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


async def buttons_menu(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    await state.clear()
    await _render_buttons_menu(callback.message)
    await callback.answer()


def _menu_spec(index: int):
    return MENU_SPECS[index] if 0 <= index < len(MENU_SPECS) else None


@operations_router.callback_query(F.data.startswith("ops:menu:item:"))
async def menu_item(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    await state.clear()
    try:
        index = int((callback.data or "").rsplit(":", 1)[-1])
    except ValueError:
        await callback.answer("نامعتبر", show_alert=True)
        return
    spec = _menu_spec(index)
    if not spec:
        await callback.answer("پیدا نشد", show_alert=True)
        return
    callback_data, default, _icon, _fallback = spec
    label = await _menu_label(callback_data, default)
    visible = await operations_service.menu_is_visible(callback_data)
    rows = [
        [await _button("تغییر نام نمایشی", f"ops:menu:rename:{index}", fallback="✏️")],
        [await _button("مخفی کردن" if visible else "نمایش دادن", f"ops:menu:toggle:{index}", fallback="👁")],
        [await _button("بازگردانی نام اصلی", f"ops:menu:reset:{index}", fallback="♻️")],
        [await _button("بازگشت", "cc:buttons", icon_key="back", fallback="⬅️")],
    ]
    await callback.message.edit_text(
        f"🔘 <b>{escape(label)}</b>\n\nCallback: <code>{escape(callback_data)}</code>\nنام اصلی: {escape(default)}\nوضعیت: {'✅ نمایش' if visible else '⛔ مخفی'}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@operations_router.callback_query(F.data.startswith("ops:menu:rename:"))
async def menu_rename_start(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    index = int((callback.data or "").rsplit(":", 1)[-1])
    spec = _menu_spec(index)
    if not spec:
        await callback.answer("پیدا نشد", show_alert=True)
        return
    await state.clear()
    await state.update_data(menu_edit_index=index)
    await state.set_state(MenuEditStates.label)
    await callback.message.answer("نام نمایشی جدید دکمه را ارسال کنید (حداکثر ۴۰ کاراکتر):")
    await callback.answer()


@operations_router.message(MenuEditStates.label, F.text)
async def menu_rename_value(message: Message, state: FSMContext):
    if not _sudo(message.from_user.id):
        return
    label = (message.text or "").strip()
    data = await state.get_data()
    spec = _menu_spec(int(data.get("menu_edit_index", -1)))
    if not spec:
        await state.clear()
        await message.answer("❌ مورد پیدا نشد.")
        return
    if not label or len(label) > 40 or "<" in label or ">" in label:
        await message.answer("❌ نام باید ۱ تا ۴۰ کاراکتر و بدون HTML باشد.")
        return
    await style_engine.set_text_override("menu", spec[0], label)
    await state.clear()
    await message.answer("✅ نام دکمه ذخیره شد.")


@operations_router.callback_query(F.data.startswith("ops:menu:toggle:"))
async def menu_visibility_toggle(callback: CallbackQuery):
    if await _deny(callback):
        return
    index = int((callback.data or "").rsplit(":", 1)[-1])
    spec = _menu_spec(index)
    if not spec:
        await callback.answer("پیدا نشد", show_alert=True)
        return
    current = await operations_service.menu_is_visible(spec[0])
    await operations_service.set_menu_visible(spec[0], not current)
    await _render_buttons_menu(callback.message)
    await callback.answer("ذخیره شد")


@operations_router.callback_query(F.data.startswith("ops:menu:reset:"))
async def menu_label_reset(callback: CallbackQuery):
    if await _deny(callback):
        return
    index = int((callback.data or "").rsplit(":", 1)[-1])
    spec = _menu_spec(index)
    if not spec:
        await callback.answer("پیدا نشد", show_alert=True)
        return
    target = next(
        (item for item in await style_engine.list_overrides() if item.scope == "menu" and item.raw_identity == spec[0]),
        None,
    )
    if target:
        await style_engine.remove_text_override(target.id)
    await _render_buttons_menu(callback.message)
    await callback.answer("بازگردانی شد")
