from html import escape
from aiogram import Router, F
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
import config
from database import db
from utils.notify import format_traffic_size, seconds_to_days
from group_ops_service import group_ops_service
from trust_service import trust_service
from order_compliance import order_compliance_service
from forced_join_service import ForcedJoinState, check_membership


public_router = Router()
@public_router.message(CommandStart(), F.text.as_('cmd'))
async def public_catch_all(message: Message, cmd: str):
    # Handle /start for non-admin non-sudo to show buy menu
    if not cmd or not cmd.startswith('/'):
        return
    if message.from_user.id in config.SUDO_ADMINS:
        return
    if await db.is_admin_authorized(message.from_user.id):
        return
    if cmd.startswith('/start'):
        await message.answer(config.MESSAGES["customer_home"], reply_markup=get_public_main_keyboard())


class PublicPaymentStates(StatesGroup):
    waiting_for_receipt = State()


def get_public_main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛒 خرید پنل نمایندگی", callback_data="public_buy_reseller")],
        [InlineKeyboardButton(text="🧪 تست رایگان", callback_data="svcmarket:trial")],
        [InlineKeyboardButton(text="🎧 پشتیبانی", callback_data="support:home")],
    ])


@public_router.callback_query(F.data == "public_buy_reseller")
async def public_buy_reseller(callback: CallbackQuery):
    plans = await db.get_plans(only_active=True)
    if not plans:
        await callback.message.edit_text(
            "در حال حاضر پلنی برای خرید موجود نیست.",
            reply_markup=get_public_main_keyboard()
        )
        await callback.answer()
        return

    lines = ["🛒 پلن‌های نمایندگی:", ""]
    for p in plans:
        traffic_txt = "نامحدود" if p.traffic_limit_bytes is None else f"{await format_traffic_size(p.traffic_limit_bytes)}"
        time_txt = "نامحدود" if p.time_limit_seconds is None else f"{seconds_to_days(p.time_limit_seconds)} روز"
        users_txt = "نامحدود" if p.max_users is None else f"{p.max_users} کاربر"
        type_txt = "حجمی" if (getattr(p, 'plan_type', 'volume') == 'volume') else "پکیجی"
        price_txt = f"{p.price:,}"
        lines.append(f"• {p.name} ({type_txt})")
        lines.append(f"  📦 ترافیک: {traffic_txt}")
        lines.append(f"  ⏱️ زمان: {time_txt}")
        lines.append(f"  👥 کاربر: {users_txt}")
        lines.append(f"  💰 قیمت: {price_txt} تومان")
        lines.append(f"   ➤ برای ثبت سفارش روی دکمه زیر بزنید: #ID {p.id}")
        lines.append("—")

    text = "\n".join(lines).rstrip("—")
    # دکمه‌های سفارش برای هر پلن
    kb_rows = []
    for p in plans:
        kb_rows.append([InlineKeyboardButton(text=f"سفارش #{p.id} - {p.name}", callback_data=f"public_order_{p.id}")])
    kb_rows.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="public_back_main")])
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    await callback.answer()


@public_router.callback_query(F.data.startswith("public_order_"))
async def public_order(callback: CallbackQuery):
    plan_id = int(callback.data.split("_")[-1])
    plans = await db.get_plans(only_active=True)
    plan = next((p for p in plans if p.id == plan_id), None)
    if not plan:
        await callback.answer("پلن یافت نشد.", show_alert=True)
        return
    order_id = await db.add_order(callback.from_user.id, plan_id, plan.price, plan.name)
    if not order_id:
        await callback.answer("خطا در ثبت سفارش.", show_alert=True)
        return
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
                f"💵 مبلغ: <b>{int(plan.price):,}</b> تومان",
            )
    except Exception:
        pass
    price_txt = f"{plan.price:,}"
    # Show manual payment cards
    cards = await db.get_cards(only_active=True)
    lines = [
        f"✅ سفارش ثبت شد.\n\nشناسه سفارش: {order_id}\nپلن: {plan.name}\nقیمت: {price_txt} تومان\n",
        config.MESSAGES["public_payment_instructions"],
        "",
        "کارت‌های فعال:",
    ]
    if not cards:
        lines.append("— فعلاً کارتی ثبت نشده. لطفاً با پشتیبانی تماس بگیرید.")
    else:
        for c in cards:
            lines.append(f"• {c.get('bank_name','بانک')} | {c.get('card_number','---- ---- ---- ----')} | {c.get('holder_name','')} ")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=config.BUTTONS["mark_paid"], callback_data=f"public_mark_paid_{order_id}")],
        [InlineKeyboardButton(text="🔙 بازگشت", callback_data="public_back_main")]
    ])
    await callback.message.edit_text("\n".join(lines), reply_markup=kb)
    await callback.answer()


@public_router.callback_query(F.data == "public_back_main")
async def public_back_main(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(config.MESSAGES["customer_home"], reply_markup=get_public_main_keyboard())
    await callback.answer()


@public_router.callback_query(F.data == "forced_join_refresh")
async def forced_join_refresh(callback: CallbackQuery):
    channels = await db.get_forced_channels()
    not_joined = []
    errors = []
    for channel in channels:
        check = await check_membership(callback.bot, channel, callback.from_user.id)
        if check.state == ForcedJoinState.NOT_JOINED:
            not_joined.append(channel)
        elif check.state == ForcedJoinState.ERROR:
            errors.append((channel, check.error))

    if not not_joined and not errors:
        await callback.message.edit_text(
            "✅ عضویت تأیید شد. حالا می‌توانید از ربات استفاده کنید.",
            reply_markup=get_public_main_keyboard(),
        )
        await callback.answer("عضویت تأیید شد ✅")
        return

    if errors:
        names = "، ".join(
            str(channel.get("title") or channel.get("chat_id")) for channel, _ in errors
        )
        await callback.answer(
            f"خطا در بررسی: {names[:120]}. مدیریت باید اتصال کانال را تست کند.",
            show_alert=True,
        )
        return

    await callback.answer("هنوز عضو همه کانال‌های الزامی نیستید.", show_alert=True)


@public_router.callback_query(F.data.startswith("public_mark_paid_"))
async def public_mark_paid(callback: CallbackQuery, state: FSMContext):
    try:
        order_id = int(callback.data.split("_")[-1])
    except (ValueError, AttributeError):
        await callback.answer("سفارش نامعتبر", show_alert=True)
        return
    order = await db.get_order_by_id(order_id)
    if not order or order.get("user_id") != callback.from_user.id or order.get("status") != "pending":
        await callback.answer("سفارش یافت نشد.", show_alert=True)
        return
    await state.update_data(order_id=order_id)
    await state.set_state(PublicPaymentStates.waiting_for_receipt)
    await callback.message.edit_text(config.MESSAGES["public_send_receipt"], reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 بازگشت", callback_data="public_back_main")]]))
    await callback.answer()


@public_router.message(PublicPaymentStates.waiting_for_receipt)
async def public_receive_payment_receipt(message: Message, state: FSMContext):
    data = await state.get_data()
    order_id = data.get("order_id")
    if not order_id:
        await state.clear()
        await message.answer("❌ سفارش پرداخت پیدا نشد؛ دوباره از سفارش وارد ارسال رسید شوید.")
        return

    photos = list(message.photo or [])
    if not photos:
        await message.answer(config.MESSAGES["public_send_receipt"])
        return
    file_id = photos[-1].file_id

    try:
        submitted = await db.submit_order_receipt(order_id, message.from_user.id, file_id)
    except Exception as exc:
        await group_ops_service.report_error(
            message.bot, exc, context=f"public receipt submit order={order_id}"
        )
        await message.answer("❌ ثبت رسید ناموفق بود. دوباره تلاش کنید.")
        return

    if not submitted:
        await state.clear()
        await message.answer("این سفارش قبلاً بررسی شده یا متعلق به شما نیست.")
        return

    order = await db.get_order_by_id(order_id)
    gate = await order_compliance_service.get(order_id)
    if gate is None and order and str(order.get("order_type") or "").lower() != "renew":
        gate = await order_compliance_service.ensure_order(
            order_id,
            message.from_user.id,
            kyc_required=not await trust_service.is_identity_approved(message.from_user.id),
        )
    if gate:
        await order_compliance_service.mark_receipt_submitted(
            order_id, message.from_user.id
        )

    plan = await db.get_plan_by_id(order.get("plan_id")) if order else None
    trusted = await trust_service.is_identity_approved(message.from_user.id)
    auto_enabled = await trust_service.auto_approve_enabled(message.from_user.id)
    verification = await trust_service.payment_verification(order_id)
    bank_state = str((verification or {}).get("state") or "unverified")

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="✅ تأیید پرداخت",
            callback_data=f"order_payment_approve_{order_id}",
        )],
        [InlineKeyboardButton(
            text="❌ رد رسید / ارسال مجدد",
            callback_data=f"order_payment_reject_{order_id}",
        )],
    ])
    caption = (
        f"🧾 <b>رسید سفارش #{order_id}</b>\n"
        f"👤 <code>{message.from_user.id}</code> "
        f"{('@' + escape(message.from_user.username)) if message.from_user.username else ''}\n"
        f"📦 {escape(plan.name if plan else (order or {}).get('plan_name_snapshot',''))}\n"
        f"💵 <b>{int((order or {}).get('price_snapshot',0)):,}</b> تومان\n"
        f"🪪 Identity: <b>{'تأیید' if trusted else 'نیازمند KYC پس از تأیید پرداخت'}</b>\n"
        f"🤖 Auto Approve: <b>{'مجاز' if auto_enabled else 'خاموش'}</b>\n"
        f"🏦 Bank Match: <b>{escape(bank_state)}</b>"
    )

    sent_to_group = False
    try:
        sent_to_group = await group_ops_service.send_photo(
            message.bot,
            "receipts",
            file_id,
            caption=caption,
            reply_markup=kb,
        )
    except Exception as exc:
        await group_ops_service.report_error(
            message.bot, exc, context=f"group receipt order={order_id}"
        )

    # Group Ops is the canonical inbox. Keep a private sudo fallback only if
    # the group is not configured or Telegram rejected the group send.
    if not sent_to_group:
        for sudo_id in config.SUDO_ADMINS:
            try:
                await message.bot.send_photo(
                    chat_id=sudo_id,
                    photo=file_id,
                    caption=caption,
                    reply_markup=kb,
                )
            except Exception:
                pass

    await state.clear()
    await message.answer(
        "✅ <b>رسید شما ثبت شد.</b>\n\n"
        "بعد از تأیید پرداخت، اگر احراز هویت شما قبلاً تأیید نشده باشد "
        "بات مرحله KYC را برایتان باز می‌کند. تا تکمیل این مراحل پنل صادر نمی‌شود.",
        reply_markup=get_public_main_keyboard(),
    )


