from __future__ import annotations

from html import escape
import math
import time
from typing import Any

from aiogram import F, Router
from aiogram.filters import Command, Filter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

import config
from database import db
from operations_service import DiscountQuote, OperationsError, operations_service
from style_engine import style_engine
from trial_experience_service import trial_experience_service
from utils.rebecca import credential_message


trial_experience_router = Router(name="trial_experience")


class PurchaseStates(StatesGroup):
    username = State()
    discount_code = State()


class PanelTrialUserStates(StatesGroup):
    username = State()


class PanelTrialSettingStates(StatesGroup):
    traffic_gb = State()
    duration_hours = State()
    max_users = State()
    cooldown_hours = State()


class PreferredOrderApproval(Filter):
    async def __call__(self, callback: CallbackQuery) -> bool | dict[str, Any]:
        data = str(callback.data or "")
        if not (data.startswith("order_approve_") or data.startswith("order_retry_")):
            return False
        try:
            order_id = int(data.rsplit("_", 1)[-1])
        except ValueError:
            return False
        username = await trial_experience_service.get_order_username(order_id)
        if not username:
            return False
        order = await db.get_order_by_id(order_id)
        if not order or str(order.get("order_type") or "").lower().startswith("renew"):
            return False
        return {"preferred_order_id": order_id}


async def _button(text: str, callback_data: str, *, icon_key: str | None = None, fallback: str | None = None):
    return await style_engine.styled_button(
        text,
        callback_data=callback_data,
        icon_key=icon_key,
        fallback=fallback,
    )


async def _heading(icon_key: str, text: str, fallback: str) -> str:
    return await style_engine.decorate_text(icon_key, text, fallback=fallback)


async def _plan_label(plan: Any) -> str:
    raw_id = str(plan.id)
    try:
        _raw, by_id = await style_engine.resolve_visual_alias("plan", raw_id)
        if by_id != raw_id:
            return by_id
    except Exception:
        pass
    try:
        raw_name = str(plan.name)
        _raw, by_name = await style_engine.resolve_visual_alias("plan", raw_name)
        if by_name != raw_name:
            return by_name
    except Exception:
        pass
    return str(plan.name)


def _is_sudo(user_id: int) -> bool:
    return int(user_id) in config.SUDO_ADMINS


async def _deny(callback: CallbackQuery) -> bool:
    if _is_sudo(callback.from_user.id):
        return False
    await callback.answer("غیرمجاز", show_alert=True)
    return True


# ---------------------------- Paid panel purchase username ----------------------------

async def _start_purchase(callback: CallbackQuery, state: FSMContext, source: str, plan_id: int) -> None:
    plan = await db.get_plan_by_id(int(plan_id))
    if not plan or not getattr(plan, "is_active", True):
        await callback.answer("پلن یافت نشد.", show_alert=True)
        return
    await state.clear()
    await state.update_data(purchase_source=source, purchase_plan_id=int(plan_id))
    await state.set_state(PurchaseStates.username)
    back_cb = "back_to_admin_main" if source == "a" else "public_back_main"
    text = (
        f"🛒 <b>{escape(str(plan.name))}</b>\n\n"
        "نام کاربری دلخواه پنل را ارسال کنید.\n"
        "رمز عبور بعد از تایید سفارش به‌صورت امن توسط ربات ساخته می‌شود.\n\n"
        "قواعد نام کاربری:\n"
        "• ۳ تا ۳۲ کاراکتر\n"
        "• حروف انگلیسی کوچک و عدد\n"
        "• نقطه، خط تیره و آندرلاین مجاز است\n\n"
        "مثال: <code>arman_panel</code>"
    )
    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            await _button("بازگشت", back_cb, icon_key="back", fallback="⬅️")
        ]]),
    )
    await callback.answer()


@trial_experience_router.callback_query(F.data.startswith("public_order_"))
async def preferred_public_order(callback: CallbackQuery, state: FSMContext):
    try:
        plan_id = int((callback.data or "").rsplit("_", 1)[-1])
    except ValueError:
        await callback.answer("پلن نامعتبر", show_alert=True)
        return
    await _start_purchase(callback, state, "p", plan_id)


@trial_experience_router.callback_query(F.data.startswith("admin_order_"))
async def preferred_admin_order(callback: CallbackQuery, state: FSMContext):
    try:
        plan_id = int((callback.data or "").rsplit("_", 1)[-1])
    except ValueError:
        await callback.answer("پلن نامعتبر", show_alert=True)
        return
    await _start_purchase(callback, state, "a", plan_id)


@trial_experience_router.message(PurchaseStates.username, F.text)
async def preferred_purchase_username(message: Message, state: FSMContext):
    try:
        username = trial_experience_service.validate_username(message.text or "")
        available = await trial_experience_service.username_available(username)
    except OperationsError as exc:
        await message.answer(f"❌ {escape(str(exc))}")
        return
    except Exception:
        await message.answer("❌ بررسی نام کاربری از پنل اصلی ناموفق بود؛ کمی بعد دوباره تلاش کنید.")
        return
    if not available:
        await message.answer("❌ این نام کاربری قبلاً استفاده شده است. یک نام دیگر بفرستید.")
        return
    data = await state.get_data()
    plan_id = int(data.get("purchase_plan_id") or 0)
    source = str(data.get("purchase_source") or "p")
    plan = await db.get_plan_by_id(plan_id)
    if not plan or not getattr(plan, "is_active", True):
        await state.clear()
        await message.answer("❌ پلن دیگر در دسترس نیست.")
        return
    await state.update_data(purchase_username=username)
    if await operations_service.has_active_discounts(int(plan.price)):
        await message.answer(
            f"✅ نام کاربری: <code>{escape(username)}</code>\n\n"
            f"قیمت پلن: <b>{int(plan.price):,} تومان</b>\n"
            "اگر کد تخفیف دارید وارد کنید.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [await _button("اعمال کد تخفیف", "ux:checkout:code", fallback="🎟")],
                [await _button("ادامه بدون تخفیف", "ux:checkout:nocode", fallback="➡️")],
            ]),
        )
        return
    await state.clear()
    await _create_order_and_render(message, source, plan_id, username, None)


@trial_experience_router.callback_query(F.data == "ux:checkout:nocode")
async def preferred_checkout_nocode(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    plan_id = int(data.get("purchase_plan_id") or 0)
    source = str(data.get("purchase_source") or "p")
    username = str(data.get("purchase_username") or "")
    if not plan_id or not username:
        await callback.answer("اطلاعات خرید منقضی شده؛ دوباره خرید را شروع کنید.", show_alert=True)
        return
    await state.clear()
    await _create_order_and_render(callback.message, source, plan_id, username, None)
    await callback.answer()


@trial_experience_router.callback_query(F.data == "ux:checkout:code")
async def preferred_checkout_code_start(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not data.get("purchase_plan_id") or not data.get("purchase_username"):
        await callback.answer("اطلاعات خرید منقضی شده؛ دوباره خرید را شروع کنید.", show_alert=True)
        return
    await state.set_state(PurchaseStates.discount_code)
    await callback.message.answer("🎟 کد تخفیف را ارسال کنید:")
    await callback.answer()


@trial_experience_router.message(PurchaseStates.discount_code, F.text)
async def preferred_checkout_code_value(message: Message, state: FSMContext):
    data = await state.get_data()
    plan_id = int(data.get("purchase_plan_id") or 0)
    source = str(data.get("purchase_source") or "p")
    username = str(data.get("purchase_username") or "")
    plan = await db.get_plan_by_id(plan_id)
    if not plan or not getattr(plan, "is_active", True) or not username:
        await state.clear()
        await message.answer("❌ اطلاعات خرید منقضی شده؛ دوباره شروع کنید.")
        return
    try:
        quote = await operations_service.quote_discount(message.text or "", message.from_user.id, int(plan.price))
    except OperationsError as exc:
        await message.answer(f"❌ {escape(str(exc))}\n\nکد دیگری بفرستید یا /start را بزنید.")
        return
    await state.clear()
    await _create_order_and_render(message, source, plan_id, username, quote)


async def _notify_free_order(bot: Any, order_id: int, user_id: int, plan_name: str, username: str, quote: DiscountQuote | None) -> None:
    rows = [[
        await _button("تایید و صدور", f"order_approve_{order_id}", fallback="✅"),
        await _button("رد", f"order_reject_{order_id}", fallback="❌"),
    ]]
    discount = f"\n🎟 کد: <code>{escape(quote.code)}</code>" if quote else ""
    for sudo_id in config.SUDO_ADMINS:
        try:
            await bot.send_message(
                int(sudo_id),
                f"🧾 <b>سفارش بدون نیاز به پرداخت #{order_id}</b>\n"
                f"👤 <code>{user_id}</code>\n📦 {escape(plan_name)}\n"
                f"🔐 Username: <code>{escape(username)}</code>{discount}",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
            )
        except Exception:
            pass


async def _create_order_and_render(
    message: Message,
    source: str,
    plan_id: int,
    username: str,
    quote: DiscountQuote | None,
) -> None:
    plan = await db.get_plan_by_id(int(plan_id))
    if not plan or not getattr(plan, "is_active", True):
        await message.answer("❌ پلن یافت نشد.")
        return
    try:
        username = trial_experience_service.validate_username(username)
        if not await trial_experience_service.username_available(username):
            await message.answer("❌ این نام کاربری دیگر در دسترس نیست؛ خرید را دوباره شروع کنید و نام دیگری انتخاب کنید.")
            return
    except Exception:
        await message.answer("❌ بررسی نهایی نام کاربری ناموفق بود؛ دوباره تلاش کنید.")
        return

    original_price = int(plan.price)
    final_price = quote.final_price if quote else original_price
    order_id = await db.add_order(message.from_user.id, int(plan_id), final_price, str(plan.name))
    if not order_id:
        await message.answer("❌ خطا در ثبت سفارش.")
        return
    try:
        await trial_experience_service.save_order_username(order_id, username)
        if quote:
            await operations_service.record_redemption(quote, message.from_user.id, order_id)
            await db.update_order(
                order_id,
                payment_note=f"discount={quote.code};original={quote.original_price};discount={quote.discount_amount}",
            )
    except Exception:
        await db.update_order(order_id, status="cancelled")
        await message.answer("❌ ذخیره اطلاعات سفارش ناموفق بود؛ سفارش لغو شد.")
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
        await message.answer(
            f"✅ سفارش #{order_id} ثبت شد.\n"
            f"📦 {escape(str(plan.name))}\n"
            f"🔐 نام کاربری: <code>{escape(username)}</code>{discount_lines}\n"
            "💵 مبلغ نهایی: <b>0 تومان</b>\n\nبرای تایید و صدور به مدیریت ارسال شد."
        )
        await _notify_free_order(message.bot, order_id, message.from_user.id, str(plan.name), username, quote)
        return

    cards = await db.get_cards(only_active=True)
    lines = [
        "✅ سفارش ثبت شد.",
        "",
        f"شناسه سفارش: <b>{order_id}</b>",
        f"پلن: {escape(str(plan.name))}",
        f"نام کاربری دلخواه: <code>{escape(username)}</code>{discount_lines}",
        f"قیمت نهایی: <b>{final_price:,} تومان</b>",
        "",
        config.MESSAGES["public_payment_instructions"],
        "",
        "کارت‌های فعال:",
    ]
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
    await message.answer(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [await _button(config.BUTTONS["mark_paid"], f"public_mark_paid_{order_id}", fallback="✅")],
            [await _button("بازگشت", back_cb, icon_key="back", fallback="⬅️")],
        ]),
    )


@trial_experience_router.callback_query(PreferredOrderApproval())
async def approve_order_with_requested_username(callback: CallbackQuery, preferred_order_id: int):
    if not _is_sudo(callback.from_user.id):
        await callback.answer("غیرمجاز", show_alert=True)
        return
    try:
        result = await trial_experience_service.approve_preferred_order(
            preferred_order_id, callback.from_user.id, callback.bot
        )
    except OperationsError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    try:
        await callback.bot.send_message(
            result.user_id,
            trial_experience_service.credential_text(result),
            parse_mode="HTML",
        )
    except Exception:
        pass
    await callback.message.edit_text(
        f"✅ سفارش #{preferred_order_id} تایید و پنل با نام کاربری <code>{escape(result.username)}</code> صادر شد."
    )
    await callback.answer("صادر شد")


# ---------------------------- Public trial experience ----------------------------

async def _render_trial_plans(message: Message, trial_type: str) -> None:
    plans = await trial_experience_service.list_trial_plans(trial_type)
    if trial_type == "panel":
        heading = await _heading("panel", "دریافت تست پنل", "🧩")
        description = "یکی از پنل‌های زیر را برای ساخت پنل نمایندگی تست انتخاب کنید."
        prefix = "ux:paneltrial:plan:"
        icon_key, fallback = "panel", "🧩"
    else:
        heading = await _heading("test", "تست رایگان کانفیگ", "🧪")
        description = "پنلی که می‌خواهید کانفیگ تست از آن ساخته شود انتخاب کنید."
        prefix = "ux:configtrial:plan:"
        icon_key, fallback = "test", "🧪"
    rows = []
    for plan in plans:
        rows.append([
            await _button(await _plan_label(plan), f"{prefix}{int(plan.id)}", icon_key=icon_key, fallback=fallback)
        ])
    if not rows:
        description += "\n\nفعلاً پنلی برای این نوع تست فعال نشده است."
    rows.append([await _button("بازگشت", "public_back_main", icon_key="back", fallback="⬅️")])
    await message.edit_text(f"{heading}\n\n{description}", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@trial_experience_router.callback_query(F.data == "ops:paneltrial:request")
async def public_panel_trial_request(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await _render_trial_plans(callback.message, "panel")
    await callback.answer()


@trial_experience_router.message(Command("paneltest"))
async def public_panel_trial_command(message: Message, state: FSMContext):
    await state.clear()
    plans = await trial_experience_service.list_trial_plans("panel")
    rows = [[
        await _button(await _plan_label(plan), f"ux:paneltrial:plan:{int(plan.id)}", icon_key="panel", fallback="🧩")
    ] for plan in plans]
    await message.answer(
        f"{await _heading('panel', 'دریافت تست پنل', '🧩')}\n\nیکی از پنل‌ها را انتخاب کنید:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@trial_experience_router.callback_query(F.data.startswith("ux:paneltrial:plan:"))
async def panel_trial_plan(callback: CallbackQuery, state: FSMContext):
    try:
        plan_id = int((callback.data or "").rsplit(":", 1)[-1])
    except ValueError:
        await callback.answer("نامعتبر", show_alert=True)
        return
    plan = await db.get_plan_by_id(plan_id)
    if not plan or not getattr(plan, "is_active", True) or not await trial_experience_service.plan_enabled(plan_id, "panel"):
        await callback.answer("این پنل برای تست در دسترس نیست.", show_alert=True)
        return
    settings = await trial_experience_service.get_panel_trial_settings()
    if not settings["enabled"]:
        await callback.answer("تست پنل فعلاً غیرفعال است.", show_alert=True)
        return
    wait = await trial_experience_service.panel_trial_wait_seconds(callback.from_user.id)
    if wait > 0:
        hours = max(1, math.ceil(wait / 3600))
        await callback.answer(f"برای تست بعدی حدود {hours} ساعت صبر کنید.", show_alert=True)
        return
    await state.clear()
    await state.update_data(panel_trial_plan_id=plan_id)
    await state.set_state(PanelTrialUserStates.username)
    await callback.message.edit_text(
        f"🧩 <b>{escape(await _plan_label(plan))}</b>\n\n"
        "نام کاربری دلخواه پنل تست را بفرستید.\n"
        "رمز عبور را ربات خودش می‌سازد.\n\n"
        "مثال: <code>arman_test</code>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            await _button("بازگشت", "ops:paneltrial:request", icon_key="back", fallback="⬅️")
        ]]),
    )
    await callback.answer()


@trial_experience_router.message(PanelTrialUserStates.username, F.text)
async def panel_trial_username(message: Message, state: FSMContext):
    data = await state.get_data()
    plan_id = int(data.get("panel_trial_plan_id") or 0)
    try:
        username = trial_experience_service.validate_username(message.text or "")
        result = await trial_experience_service.issue_panel_trial(
            user_id=message.from_user.id,
            plan_id=plan_id,
            requested_username=username,
            telegram_username=message.from_user.username,
            first_name=message.from_user.first_name,
            last_name=message.from_user.last_name,
        )
    except OperationsError as exc:
        await message.answer(f"❌ {escape(str(exc))}")
        return
    except Exception:
        await message.answer("❌ ساخت پنل تست ناموفق بود؛ کمی بعد دوباره تلاش کنید.")
        return
    await state.clear()
    await message.answer(
        credential_message(result["username"], result["password"], result["login_url"], f"تست {result['plan_name']}"),
        parse_mode="HTML",
    )


@trial_experience_router.callback_query(F.data == "ops:configtrial:request")
@trial_experience_router.callback_query(F.data == "ops:trial:request")
async def public_config_trial_request(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await _render_trial_plans(callback.message, "config")
    await callback.answer()


@trial_experience_router.message(Command("test"))
async def public_config_trial_command(message: Message, state: FSMContext):
    await state.clear()
    plans = await trial_experience_service.list_trial_plans("config")
    rows = [[
        await _button(await _plan_label(plan), f"ux:configtrial:plan:{int(plan.id)}", icon_key="test", fallback="🧪")
    ] for plan in plans]
    await message.answer(
        f"{await _heading('test', 'تست رایگان کانفیگ', '🧪')}\n\nپنل موردنظر را انتخاب کنید:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@trial_experience_router.callback_query(F.data.startswith("ux:configtrial:plan:"))
async def config_trial_plan(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        plan_id = int((callback.data or "").rsplit(":", 1)[-1])
    except ValueError:
        await callback.answer("نامعتبر", show_alert=True)
        return
    if not await trial_experience_service.plan_enabled(plan_id, "config"):
        await callback.answer("این پنل برای تست کانفیگ فعال نیست.", show_alert=True)
        return
    try:
        options = await trial_experience_service.config_service_options(plan_id)
    except OperationsError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    if not options:
        await callback.answer("برای این پنل سرویس قابل استفاده‌ای تعریف نشده است.", show_alert=True)
        return
    if len(options) == 1:
        await _issue_config_trial(callback.message, callback.from_user.id, plan_id, int(options[0]["service_id"]) or None)
        await callback.answer()
        return
    rows = []
    for item in options:
        service_id = int(item["service_id"])
        try:
            raw = str(service_id)
            _raw, label = await style_engine.resolve_visual_alias("service", raw)
            if label == raw:
                label = str(item["name"])
        except Exception:
            label = str(item["name"])
        rows.append([
            await _button(label, f"ux:configtrial:issue:{plan_id}:{service_id}", icon_key="test", fallback="🧪")
        ])
    rows.append([await _button("بازگشت", "ops:configtrial:request", icon_key="back", fallback="⬅️")])
    await callback.message.edit_text(
        f"{await _heading('test', 'انتخاب سرویس تست', '🧪')}\n\nسرویس موردنظر را انتخاب کنید:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@trial_experience_router.callback_query(F.data.startswith("ux:configtrial:issue:"))
async def config_trial_issue(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    parts = (callback.data or "").split(":")
    if len(parts) != 5:
        await callback.answer("نامعتبر", show_alert=True)
        return
    try:
        plan_id, service_id = int(parts[3]), int(parts[4])
    except ValueError:
        await callback.answer("نامعتبر", show_alert=True)
        return
    await _issue_config_trial(callback.message, callback.from_user.id, plan_id, service_id or None)
    await callback.answer()


async def _issue_config_trial(message: Message, user_id: int, plan_id: int, service_id: int | None) -> None:
    try:
        result = await trial_experience_service.issue_config_trial(user_id, plan_id, service_id)
    except OperationsError as exc:
        await message.answer(f"❌ {escape(str(exc))}")
        return
    duration = max(1, (int(result["expire_at"]) - int(time.time())) // 60)
    traffic_gb = result["traffic_bytes"] / (1024**3)
    lines = [
        f"{await _heading('test', 'کانفیگ تست شما آماده است', '🧪')}",
        "",
        f"پنل: <b>{escape(str(result['plan_name']))}</b>",
        f"نام کاربری: <code>{escape(str(result['username']))}</code>",
        f"حجم: <b>{traffic_gb:g} GB</b>",
        f"اعتبار تقریبی: <b>{duration} دقیقه</b>",
    ]
    if result.get("subscription_url"):
        lines.extend(["", "لینک سابسکریپشن:", f"<code>{escape(str(result['subscription_url']))}</code>"])
    elif result.get("links"):
        lines.extend(["", "لینک:", f"<code>{escape(str(result['links'][0]))}</code>"])
    else:
        lines.extend(["", "کانفیگ ساخته شد اما لینک سابسکریپشن برنگشت؛ با پشتیبانی تماس بگیرید."])
    await message.answer("\n".join(lines))


# ---------------------------- SUDO trial center ----------------------------

async def _render_trial_center(message: Message) -> None:
    config_settings = await operations_service.get_trial_settings()
    panel_settings = await trial_experience_service.get_panel_trial_settings()
    text = (
        "🧪 <b>مرکز تست‌ها</b>\n\n"
        f"کانفیگ رایگان: {'✅ فعال' if config_settings['enabled'] else '⛔ غیرفعال'}\n"
        f"تست پنل: {'✅ فعال' if panel_settings['enabled'] else '⛔ غیرفعال'}\n\n"
        "دو سیستم مستقل هستند؛ کاربر برای هرکدام پنل/پلن موردنظر را انتخاب می‌کند."
    )
    await message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [await _button("تنظیم تست رایگان کانفیگ", "ux:trialadmin:config", icon_key="test", fallback="🧪")],
            [await _button("تنظیم تست پنل", "ux:trialadmin:panel", icon_key="panel", fallback="🧩")],
            [await _button("انتخاب پنل‌های قابل تست", "ux:trialadmin:plans", fallback="📦")],
            [await _button("خانه", "back_to_main", icon_key="home", fallback="🏠")],
        ]),
    )


@trial_experience_router.callback_query(F.data == "cc:test")
async def trial_center(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    await state.clear()
    await _render_trial_center(callback.message)
    await callback.answer()


@trial_experience_router.callback_query(F.data == "ux:trialadmin:config")
async def config_trial_admin(callback: CallbackQuery):
    if await _deny(callback):
        return
    settings = await operations_service.get_trial_settings()
    traffic_gb = settings["traffic_bytes"] / 1024**3
    minutes = settings["duration_seconds"] // 60
    cooldown = settings["cooldown_seconds"] / 3600
    await callback.message.edit_text(
        "🧪 <b>تست رایگان کانفیگ</b>\n\n"
        f"وضعیت: {'✅ فعال' if settings['enabled'] else '⛔ غیرفعال'}\n"
        f"حجم: <b>{traffic_gb:g} GB</b>\n"
        f"مدت: <b>{minutes} دقیقه</b>\n"
        f"فاصله دریافت: <b>{cooldown:g} ساعت</b>\n\n"
        "انتخاب پنل/سرویس هنگام دریافت توسط خود کاربر انجام می‌شود.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [await _button("خاموش کردن" if settings["enabled"] else "فعال کردن", "ops:trial:toggle", fallback="🔁")],
            [
                await _button("تنظیم حجم", "ops:trial:traffic", fallback="📦"),
                await _button("تنظیم مدت", "ops:trial:duration", fallback="⏱"),
            ],
            [await _button("تنظیم فاصله دریافت", "ops:trial:cooldown", fallback="🕒")],
            [await _button("پلن‌های قابل تست", "ux:trialadmin:plans", fallback="📦")],
            [await _button("بازگشت", "cc:test", icon_key="back", fallback="⬅️")],
        ]),
    )
    await callback.answer()


async def _render_panel_trial_admin(message: Message) -> None:
    settings = await trial_experience_service.get_panel_trial_settings()
    await message.edit_text(
        "🧩 <b>تست پنل نمایندگی</b>\n\n"
        f"وضعیت: {'✅ فعال' if settings['enabled'] else '⛔ غیرفعال'}\n"
        f"حجم کل پنل تست: <b>{settings['traffic_bytes'] / 1024**3:g} GB</b>\n"
        f"اعتبار: <b>{settings['duration_seconds'] / 3600:g} ساعت</b>\n"
        f"حداکثر کاربر: <b>{settings['max_users']}</b>\n"
        f"فاصله دریافت مجدد: <b>{settings['cooldown_seconds'] / 3600:g} ساعت</b>\n\n"
        "رمز عبور همیشه توسط ربات ساخته می‌شود و کاربر فقط نام کاربری دلخواه را وارد می‌کند.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [await _button("خاموش کردن" if settings["enabled"] else "فعال کردن", "ux:paneltrial:toggle", fallback="🔁")],
            [
                await _button("حجم", "ux:paneltrial:set:traffic", fallback="📦"),
                await _button("اعتبار", "ux:paneltrial:set:duration", fallback="⏱"),
            ],
            [
                await _button("حد کاربر", "ux:paneltrial:set:users", fallback="👥"),
                await _button("Cooldown", "ux:paneltrial:set:cooldown", fallback="🕒"),
            ],
            [await _button("پلن‌های قابل تست", "ux:trialadmin:plans", fallback="📦")],
            [await _button("بازگشت", "cc:test", icon_key="back", fallback="⬅️")],
        ]),
    )


@trial_experience_router.callback_query(F.data == "ux:trialadmin:panel")
async def panel_trial_admin(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    await state.clear()
    await _render_panel_trial_admin(callback.message)
    await callback.answer()


@trial_experience_router.callback_query(F.data == "ux:paneltrial:toggle")
async def panel_trial_toggle(callback: CallbackQuery):
    if await _deny(callback):
        return
    settings = await trial_experience_service.get_panel_trial_settings()
    await trial_experience_service.set_panel_trial_setting("enabled", not settings["enabled"])
    await _render_panel_trial_admin(callback.message)
    await callback.answer("ذخیره شد")


@trial_experience_router.callback_query(F.data.startswith("ux:paneltrial:set:"))
async def panel_trial_setting_start(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    key = (callback.data or "").rsplit(":", 1)[-1]
    await state.clear()
    prompts = {
        "traffic": (PanelTrialSettingStates.traffic_gb, "حجم کل پنل تست را به GB بفرستید؛ مثال: <code>10</code>"),
        "duration": (PanelTrialSettingStates.duration_hours, "اعتبار پنل تست را به ساعت بفرستید؛ مثال: <code>24</code>"),
        "users": (PanelTrialSettingStates.max_users, "حداکثر تعداد کاربر پنل تست را بفرستید؛ مثال: <code>5</code>"),
        "cooldown": (PanelTrialSettingStates.cooldown_hours, "فاصله دریافت مجدد را به ساعت بفرستید؛ مثال: <code>168</code>"),
    }
    if key not in prompts:
        await callback.answer("نامعتبر", show_alert=True)
        return
    target_state, prompt = prompts[key]
    await state.set_state(target_state)
    await callback.message.answer(prompt)
    await callback.answer()


@trial_experience_router.message(PanelTrialSettingStates.traffic_gb, F.text)
async def panel_trial_traffic_value(message: Message, state: FSMContext):
    if not _is_sudo(message.from_user.id):
        return
    try:
        value = float((message.text or "").replace(",", "."))
    except ValueError:
        await message.answer("❌ عدد معتبر بفرستید.")
        return
    if not 0.1 <= value <= 10000:
        await message.answer("❌ حجم باید بین 0.1 تا 10000 گیگ باشد.")
        return
    await trial_experience_service.set_panel_trial_setting("traffic_bytes", int(value * 1024**3))
    await state.clear()
    await message.answer("✅ حجم تست پنل ذخیره شد.")


@trial_experience_router.message(PanelTrialSettingStates.duration_hours, F.text)
async def panel_trial_duration_value(message: Message, state: FSMContext):
    if not _is_sudo(message.from_user.id):
        return
    try:
        value = float((message.text or "").replace(",", "."))
    except ValueError:
        await message.answer("❌ عدد معتبر بفرستید.")
        return
    if not 0.1 <= value <= 720:
        await message.answer("❌ اعتبار باید بین 0.1 تا 720 ساعت باشد.")
        return
    await trial_experience_service.set_panel_trial_setting("duration_seconds", int(value * 3600))
    await state.clear()
    await message.answer("✅ اعتبار تست پنل ذخیره شد.")


@trial_experience_router.message(PanelTrialSettingStates.max_users, F.text)
async def panel_trial_users_value(message: Message, state: FSMContext):
    if not _is_sudo(message.from_user.id):
        return
    try:
        value = int((message.text or "").strip())
    except ValueError:
        await message.answer("❌ عدد صحیح بفرستید.")
        return
    if not 1 <= value <= 1000:
        await message.answer("❌ تعداد کاربر باید بین ۱ تا ۱۰۰۰ باشد.")
        return
    await trial_experience_service.set_panel_trial_setting("max_users", value)
    await state.clear()
    await message.answer("✅ حد کاربر تست پنل ذخیره شد.")


@trial_experience_router.message(PanelTrialSettingStates.cooldown_hours, F.text)
async def panel_trial_cooldown_value(message: Message, state: FSMContext):
    if not _is_sudo(message.from_user.id):
        return
    try:
        value = float((message.text or "").replace(",", "."))
    except ValueError:
        await message.answer("❌ عدد معتبر بفرستید.")
        return
    if not 0 <= value <= 8760:
        await message.answer("❌ فاصله باید بین ۰ تا ۸۷۶۰ ساعت باشد.")
        return
    await trial_experience_service.set_panel_trial_setting("cooldown_seconds", int(value * 3600))
    await state.clear()
    await message.answer("✅ فاصله دریافت تست پنل ذخیره شد.")


async def _render_trial_plan_access(message: Message) -> None:
    plans = await db.get_plans(only_active=True)
    rows = []
    lines = [
        "📦 <b>پنل‌های قابل تست</b>",
        "",
        "برای هر پلن مشخص کنید در «تست پنل» و «تست رایگان کانفیگ» نمایش داده شود یا نه.",
        "",
    ]
    for plan in plans:
        panel_on = await trial_experience_service.plan_enabled(int(plan.id), "panel")
        config_on = await trial_experience_service.plan_enabled(int(plan.id), "config")
        label = await _plan_label(plan)
        lines.append(f"• {escape(label)}")
        rows.append([
            await _button(
                f"{'✅' if panel_on else '⛔'} پنل",
                f"ux:trialadmin:plan:panel:{int(plan.id)}",
                fallback="🧩",
            ),
            await _button(
                f"{'✅' if config_on else '⛔'} کانفیگ",
                f"ux:trialadmin:plan:config:{int(plan.id)}",
                fallback="🧪",
            ),
        ])
    if not plans:
        lines.append("پلن فعالی وجود ندارد.")
    rows.append([await _button("بازگشت", "cc:test", icon_key="back", fallback="⬅️")])
    await message.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@trial_experience_router.callback_query(F.data == "ux:trialadmin:plans")
async def trial_plan_access(callback: CallbackQuery):
    if await _deny(callback):
        return
    await _render_trial_plan_access(callback.message)
    await callback.answer()


@trial_experience_router.callback_query(F.data.startswith("ux:trialadmin:plan:"))
async def trial_plan_access_toggle(callback: CallbackQuery):
    if await _deny(callback):
        return
    parts = (callback.data or "").split(":")
    if len(parts) != 5 or parts[3] not in {"panel", "config"}:
        await callback.answer("نامعتبر", show_alert=True)
        return
    trial_type = parts[3]
    try:
        plan_id = int(parts[4])
    except ValueError:
        await callback.answer("نامعتبر", show_alert=True)
        return
    current = await trial_experience_service.plan_enabled(plan_id, trial_type)
    await trial_experience_service.set_plan_enabled(plan_id, trial_type, not current)
    await _render_trial_plan_access(callback.message)
    await callback.answer("ذخیره شد")
