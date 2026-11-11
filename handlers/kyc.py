from __future__ import annotations

from html import escape
import re

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from trust_service import trust_service
from style_engine import style_engine
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import config
from database import db
from kyc_service import kyc_service
from group_ops_service import group_ops_service
from order_kyc_gate import order_kyc_gate
from payg_kyc_gate import payg_kyc_gate
from payg_service import payg_service
from rebecca_catalog import get_service_by_rebecca_id
from utils.rebecca import credential_message


kyc_router = Router(name="kyc")


class KycStates(StatesGroup):
    waiting_full_name = State()
    waiting_card_digits = State()
    waiting_document = State()


class ManualTrustStates(StatesGroup):
    telegram_user_id = State()
    revoke_telegram_user_id = State()


def _card_digits(text: str) -> tuple[str, str] | None:
    """Accept only eight displayed digits, never a complete 16-digit PAN."""
    source = str(text or "").translate(str.maketrans(
        "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"
    ))
    stripped = re.sub(r"[\\s\\-]", "", source)
    if len(stripped) != 8 or not stripped.isascii() or not stripped.isdigit():
        return None
    return stripped[:4], stripped[-4:]


def _user_status_text(profile: dict | None) -> str:
    if not profile:
        return "⚪ ثبت نشده"
    status = str(profile.get("status") or "")
    return {
        "pending": "🟡 در انتظار بررسی",
        "approved": "🟢 تأیید شده",
        "rejected": "🔴 رد شده",
    }.get(status, "⚪ نامشخص")


async def _home_callback(user_id: int) -> str:
    if int(user_id) in config.SUDO_ADMINS:
        return "back_to_main"
    if await db.is_admin_authorized(int(user_id)):
        return "back_to_admin_main"
    return "public_back_main"


def kyc_intro_text(profile: dict | None, telegram_user_id: int) -> str:
    """Short, consent-aware KYC onboarding for one numeric Telegram identity."""
    status = str((profile or {}).get("status") or "not_submitted")
    intro = (
        "🪪 <b>احراز هویت حساب</b>\n"
        f"🆔 شناسه تلگرام: <code>{int(telegram_user_id)}</code>\n"
        f"📌 وضعیت: <b>{_user_status_text(profile)}</b>\n\n"
    )
    if status == "approved":
        return intro + (
            "✅ هویت شما تأیید شده است. نیازی به ارسال مجدد مدرک ندارید.\n"
            "از منوی اصلی می‌توانید خدمات خود را مدیریت کنید."
        )
    if status == "pending":
        return intro + (
            "⏳ مدارک شما ثبت شده و در صف بررسی مدیر قرار دارد.\n"
            "تا پایان بررسی، مدرک دیگری ارسال نکنید.\n\n"
            "پس از تصمیم مدیر، نتیجه در همین گفتگوی خصوصی اعلام می‌شود."
        )
    if status == "rejected":
        reason = escape(str(profile.get("rejection_reason") or "تصویر یا اطلاعات قابل تأیید نبود."))
        note = f"⚠️ علت: {reason}\n\n"
    else:
        note = ""
    return intro + note + (
        "<b>مراحل احراز هویت</b>\n"
        "۱. نام و نام خانوادگی واقعی را وارد کنید.\n"
        "۲. یک عکس خوانا از مدرک هویتی بفرستید.\n"
        "۳. درخواست شما توسط مدیر بررسی می‌شود.\n\n"
        "🔒 اطلاعات فقط برای بررسی هویت استفاده می‌شود؛ تصویر در سرور ربات ذخیره نمی‌شود "
        "اما برای مدیران مجاز در تلگرام ارسال می‌شود.\n"
        "احراز هویت برای صدور سرویس‌های نیازمند بررسی الزامی است."
    )


def kyc_entry_keyboard(profile: dict | None, *, has_admin: bool = False) -> InlineKeyboardMarkup:
    status = str((profile or {}).get("status") or "not_submitted")
    rows = []
    if status in {"not_submitted", "rejected"}:
        rows.append([InlineKeyboardButton(text="🪪 شروع احراز هویت", callback_data="kyc:start")])
    elif status == "pending":
        rows.append([InlineKeyboardButton(text="🔄 بررسی وضعیت درخواست", callback_data="kyc:status")])
    if not has_admin:
        rows.append([InlineKeyboardButton(text="🛒 مشاهده پلن‌ها", callback_data="public_buy_reseller")])
    rows.append([InlineKeyboardButton(text="🎧 پشتیبانی", callback_data="support:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _status_keyboard(user_id: int) -> InlineKeyboardMarkup:
    profile = await kyc_service.get_profile(user_id)
    manually_trusted = await trust_service.is_manually_trusted(user_id)
    rows = []
    if not manually_trusted and (not profile or profile.get("status") in {None, "rejected"}):
        rows.append([InlineKeyboardButton(
            text="🪪 شروع/ارسال مجدد درخواست", callback_data="kyc:start"
        )])
    rows.append([InlineKeyboardButton(
        text=config.BUTTONS["back"], callback_data=await _home_callback(user_id)
    )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@kyc_router.callback_query(F.data == "kyc:status")
async def kyc_status(callback: CallbackQuery):
    chat_type = getattr(getattr(callback.message, "chat", None), "type", "private")
    if str(getattr(chat_type, "value", chat_type)) != "private":
        await callback.answer("وضعیت احراز هویت فقط در گفتگوی خصوصی نمایش داده می‌شود.", show_alert=True)
        return
    profile = await kyc_service.get_profile(callback.from_user.id)
    lines = [kyc_intro_text(profile, callback.from_user.id)]
    if profile and profile.get("full_name"):
        lines.append(f"نام ثبت‌شده: <b>{escape(str(profile['full_name']))}</b>")
    if profile and profile.get("card_first4") and profile.get("card_last4"):
        lines.append(
            f"💳 کارت ثبت‌شده: <code>{escape(str(profile['card_first4']))} •••• •••• {escape(str(profile['card_last4']))}</code>"
        )
    if await trust_service.is_manually_trusted(callback.from_user.id):
        lines.append("✅ دسترسی شما قبلاً توسط مدیر تأیید شده؛ نیازی به ارسال مدارک نیست.")
    if profile and profile.get("status") == "rejected":
        lines.append(f"دلیل رد: {escape(str(profile.get('rejection_reason') or 'مدرک قابل تأیید نبود.'))}")
    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=await _status_keyboard(callback.from_user.id),
    )
    await callback.answer()


@kyc_router.callback_query(F.data.in_({"kyc:start", "kyc:level:2"}))
async def kyc_start(callback: CallbackQuery, state: FSMContext):
    # Explicit voluntary KYC is available before purchasing as well as after
    # payment. Fulfillment remains gated by order_kyc_gate until approval.
    chat_type = getattr(getattr(callback.message, "chat", None), "type", "private")
    if str(getattr(chat_type, "value", chat_type)) != "private":
        await callback.answer("مدارک هویتی را فقط در گفتگوی خصوصی ربات ارسال کنید.", show_alert=True)
        return
    if await trust_service.is_identity_approved(callback.from_user.id):
        await callback.answer("حسابت قبلاً تأیید شده؛ نیازی به تکرار نیست. ✅", show_alert=True)
        return
    if await kyc_service.status(callback.from_user.id) == "pending":
        await callback.answer("درخواست شما در صف بررسی است. منتظر پاسخ مدیر باشید.", show_alert=True)
        return
    level = 2 if str(callback.data or "") == "kyc:level:2" else 1
    await state.clear()
    await state.update_data(kyc_level=level)
    await state.set_state(KycStates.waiting_full_name)
    header = await style_engine.render_emoji("kyc", fallback="🪪")
    await callback.message.edit_text(
        f"{header} <b>تأیید حساب · مرحله ۱ از ۳</b>\n\n"
        "خوش اومدی! فقط یک‌بار اطلاعاتت رو بررسی می‌کنیم تا خریدهای بعدی راحت‌تر باشن. 🌿\n\n"
        "👤 <b>نام و نام خانوادگی خودت رو بفرست</b>\n"
        "لطفاً مطابق کارت بانکی یا مدرک هویتیت بنویس.\n"
        "نمونه: علی رضایی\n\n"
        "🔒 اطلاعات فقط برای بررسی مدیر استفاده می‌شن. می‌تونی هر زمان انصراف بدی.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✖️ انصراف از احراز هویت", callback_data="kyc:cancel")]
        ]),
    )
    await callback.answer()


@kyc_router.callback_query(F.data == "kyc:cancel")
async def kyc_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(
        "احراز هویت لغو شد.",
        reply_markup=await _status_keyboard(callback.from_user.id),
    )
    await callback.answer()


@kyc_router.message(KycStates.waiting_full_name, F.text)
async def kyc_full_name(message: Message, state: FSMContext):
    full_name = " ".join((message.text or "").split()).strip()
    if len(full_name) < 4 or len(full_name) > 120 or len(full_name.split()) < 2:
        await message.answer(
            "⚠️ نام و نام خانوادگی را کامل، در دو بخش یا بیشتر وارد کنید.\n"
            "مثال: علی رضایی"
        )
        return
    await state.update_data(kyc_full_name=full_name)
    data = await state.get_data()
    level = int(data.get("kyc_level") or 1)
    if level == 1:
        await state.set_state(KycStates.waiting_card_digits)
        await message.answer(
            "💳 <b>مرحله ۲ از ۳ · کارت بانکی خودت</b>\n\n"
            "ممنون! حالا فقط <b>۴ رقم اول و ۴ رقم آخر</b> کارت بانکی که ازش پرداخت می‌کنی رو بفرست.\n"
            "مثلاً: <code>6037 1234</code>\n\n"
            "⚠️ لطفاً شماره کامل کارت، رمز، CVV2 یا تاریخ انقضا نفرست.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="↩️ انصراف", callback_data="kyc:cancel")
            ]]),
        )
    else:
        await state.set_state(KycStates.waiting_document)
        await message.answer(
            "🔐 <b>مرحله ۲ از ۳ · تأیید پیشرفته</b>\n\n"
            "یک تصویر خوانا از مدرک هویتی متعلق به خودت در همین چت خصوصی بفرست.\n"
            "برای جلوگیری از افشای اطلاعات، مدارک اضافی نفرست.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="↩️ انصراف", callback_data="kyc:cancel")
            ]]),
        )


@kyc_router.message(KycStates.waiting_card_digits, F.text)
async def kyc_card_digits(message: Message, state: FSMContext):
    digits = _card_digits(message.text or "")
    if not digits:
        await message.answer(
            "⚠️ فقط هشت رقم لازمه: <b>۴ رقم اول و ۴ رقم آخر</b>.\n"
            "مثال: <code>6037 1234</code>\n"
            "شماره کامل ۱۶رقمی رو ارسال نکن."
        )
        return
    await state.update_data(kyc_first4=digits[0], kyc_last4=digits[1])
    await state.set_state(KycStates.waiting_document)
    await message.answer(
        "📷 <b>مرحله ۳ از ۳ · تصویر تأیید کارت</b>\n\n"
        "عالیه! یک عکس از <b>همون کارت متعلق به خودت</b> بفرست، اما قبلش حتماً "
        "<b>۸ رقم وسط شماره کارت، CVV2 و تاریخ انقضا رو کاملاً بپوشون</b>.\n\n"
        f"فقط این الگو باید مشخص بمونه: <code>{digits[0]} •••• •••• {digits[1]}</code>\n"
        "نام صاحب کارت هم اگر روی کارت درج شده، خوانا باشه.\n\n"
        "تصویر صرفاً برای بررسی دستی به مدیر نمایش داده می‌شه و به‌صورت فایل در سرور ذخیره نمی‌شه.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="↩️ انصراف", callback_data="kyc:cancel")
        ]]),
    )


@kyc_router.message(KycStates.waiting_document)
async def kyc_document(message: Message, state: FSMContext):
    if not message.photo:
        await message.answer(
            "📷 هنوز عکس دریافت نشده است. لطفاً تصویر مدرک را با گزینه Photo بفرستید؛ "
            "فایل یا متن جایگزین عکس پذیرفته نمی‌شود."
        )
        return
    data = await state.get_data()
    full_name = str(data.get("kyc_full_name") or "").strip()
    level = int(data.get("kyc_level") or 1)
    photo = message.photo[-1]
    await kyc_service.submit(
        message.from_user.id,
        full_name,
        photo.file_id,
        getattr(photo, "file_unique_id", None),
        verification_level=level,
        card_first4=str(data.get("kyc_first4") or "") if level == 1 else None,
        card_last4=str(data.get("kyc_last4") or "") if level == 1 else None,
    )
    await state.clear()

    await message.answer(
        "✅ <b>درخواستت ثبت شد!</b>\n\n"
        "🟡 <b>در انتظار بررسی دستی مدیر</b>\n"
        f"🆔 Telegram ID: <code>{int(message.from_user.id)}</code>\n\n"
        "نتیجه تأیید یا رد درخواست در همین گفتگو اطلاع‌رسانی می‌شود.\n"
        "تا آن زمان لازم نیست دوباره مدرک ارسال کنید.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="📋 وضعیت احراز هویت", callback_data="kyc:status")
        ]]),
    )

    review_kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ تأیید KYC", callback_data=f"kycreview:approve:{message.from_user.id}"),
        InlineKeyboardButton(text="❌ رد KYC", callback_data=f"kycreview:reject:{message.from_user.id}"),
    ]])
    caption = (
        f"🪪 <b>درخواست تأیید {'کارت بانکی' if level == 1 else 'هویت پیشرفته'}</b>\n"
        f"🆔 Telegram ID: <code>{message.from_user.id}</code>\n"
        f"🔗 یوزرنیم: {('@' + escape(message.from_user.username)) if message.from_user.username else '—'}\n"
        f"👤 نام: <b>{escape(full_name)}</b>\n"
        + (f"💳 کارت: <code>{escape(str(data.get('kyc_first4')))} •••• •••• {escape(str(data.get('kyc_last4')))}</code>\n" if level == 1 else "")
        + "⚠️ برای کارت، مطمئن شوید ارقام وسط، CVV2 و انقضا پوشیده‌اند."
    )
    try:
        await group_ops_service.send_photo(
            message.bot, "kyc", photo.file_id, caption=caption, reply_markup=review_kb
        )
    except Exception:
        pass
    for sudo_id in config.SUDO_ADMINS:
        try:
            await message.bot.send_photo(
                sudo_id,
                photo.file_id,
                caption=caption,
                reply_markup=review_kb,
            )
        except Exception:
            pass


async def _notify_review_result(callback: CallbackQuery, user_id: int, approved: bool) -> None:
    try:
        if approved:
            from purchase_kyc_flow import resume_keyboard
            markup = await resume_keyboard(user_id)
            await callback.bot.send_message(
                user_id,
                "✅ <b>حسابت تأیید شد!</b>\n\n"
                "ممنون که مراحل رو تکمیل کردی. 🌿\n"
                "از این به بعد برای خریدهای معمولی نیازی به تکرار این بررسی نداری.\n"
                "اگر سفارشی منتظر پرداخت داری، از دکمه زیر همون سفارش رو ادامه بده.",
                reply_markup=markup,
            )
        else:
            await callback.bot.send_message(
                user_id,
                "❌ <b>احراز هویت شما تأیید نشد.</b>\n"
                "می‌توانید از /start → وضعیت احراز هویت، مدرک را دوباره ارسال کنید.",
            )
    except Exception:
        pass


@kyc_router.callback_query(F.data.startswith("kycreview:approve:"))
async def kyc_review_approve(callback: CallbackQuery):
    if callback.from_user.id not in config.SUDO_ADMINS:
        await callback.answer("غیرمجاز", show_alert=True)
        return
    user_id = int((callback.data or "").split(":")[-1])
    if not await kyc_service.approve(user_id, callback.from_user.id):
        await callback.answer("این درخواست قبلاً بررسی شده است.", show_alert=True)
        return
    await callback.message.edit_caption(
        caption=(callback.message.caption or "") + "\n\n✅ <b>تأیید شد</b>",
    )
    released_orders = await order_kyc_gate.release_after_kyc(
        user_id, callback.from_user.id
    )
    released_payg = await payg_kyc_gate.release_after_kyc(
        user_id, callback.from_user.id
    )
    if released_orders:
        for order in released_orders:
            order_id = int(order["id"])
            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(
                    text="✅ صدور سفارش",
                    callback_data=f"order_approve_{order_id}",
                )
            ], [
                InlineKeyboardButton(
                    text="❌ رد / مشکل پرداخت",
                    callback_data=f"order_reject_{order_id}",
                )
            ]])
            await group_ops_service.send_text(
                callback.bot,
                "orders",
                (
                    f"✅ <b>KYC تأیید شد؛ سفارش آماده صدور است</b>\n\n"
                    f"🧾 سفارش: <code>#{order_id}</code>\n"
                    f"👤 <code>{user_id}</code>\n"
                    f"📦 {escape(str(order.get('plan_name_snapshot') or 'پنل نمایندگی'))}\n"
                    f"💵 <b>{int(order.get('price_snapshot') or 0):,}</b> تومان"
                ),
                reply_markup=kb,
            )
        try:
            await callback.bot.send_message(
                user_id,
                "✅ <b>احراز هویت شما تأیید شد.</b>\n"
                "پرداخت قبلاً تأیید شده و سفارش شما وارد صف صدور شد.",
            )
        except Exception:
            pass

    for item in released_payg:
        result = item.get("result") or {}
        account = result.get("account")
        if not account:
            continue
        admin = await db.get_admin_by_id(int(account["admin_id"]))
        if not admin:
            continue
        service_id = result.get("service_id")
        service = await get_service_by_rebecca_id(int(service_id)) if service_id else None
        login_url = admin.login_url or config.REBECCA_LOGIN_URL or config.REBECCA_URL
        try:
            await callback.bot.send_message(
                user_id,
                "✅ <b>KYC تأیید شد و پنل PAYG فعال شد.</b>\n\n"
                + credential_message(
                    str(admin.marzban_username),
                    str(admin.marzban_password),
                    str(login_url),
                    f"PAYG {service.display_name if service else 'Rebecca'}",
                )
                + f"\n\n💰 موجودی کیف پول: <b>{(await payg_service.get_balance(user_id)):,} تومان</b>",
            )
        except Exception:
            pass
        await group_ops_service.send_text(
            callback.bot,
            "payments",
            f"✅ PAYG <code>#{int(item['topup_id'])}</code> بعد از KYC فعال شد · کاربر <code>{user_id}</code>",
        )

    if not released_orders and not released_payg:
        await _notify_review_result(callback, user_id, True)
    elif released_orders and not released_payg:
        # The order-specific message above already confirms KYC and queueing.
        pass
    await group_ops_service.audit(
        actor_id=callback.from_user.id,
        action="kyc.approve",
        subject_type="user",
        subject_id=user_id,
        new_value="approved",
    )
    await group_ops_service.send_text(
        callback.bot,
        "kyc",
        f"✅ KYC کاربر <code>{user_id}</code> توسط <code>{callback.from_user.id}</code> تأیید شد.",
    )
    await callback.answer("KYC تأیید شد")


@kyc_router.callback_query(F.data.startswith("kycreview:reject:"))
async def kyc_review_reject(callback: CallbackQuery):
    if callback.from_user.id not in config.SUDO_ADMINS:
        await callback.answer("غیرمجاز", show_alert=True)
        return
    user_id = int((callback.data or "").split(":")[-1])
    if not await kyc_service.reject(user_id, callback.from_user.id):
        await callback.answer("این درخواست قبلاً بررسی شده است.", show_alert=True)
        return
    await callback.message.edit_caption(
        caption=(callback.message.caption or "") + "\n\n❌ <b>رد شد</b>",
    )
    refund_orders = await order_kyc_gate.refund_pending_after_rejection(
        user_id, callback.from_user.id
    )
    refund_payg = await payg_kyc_gate.refund_pending_after_rejection(user_id)
    if refund_orders or refund_payg:
        details = []
        if refund_orders:
            details.append("سفارش‌ها: " + ", ".join("#" + str(x) for x in refund_orders))
        if refund_payg:
            details.append("PAYG: " + ", ".join("#" + str(x) for x in refund_payg))
        await group_ops_service.send_text(
            callback.bot,
            "payments",
            (
                "↩️ <b>استرداد وجه نیازمند بررسی</b>\n\n"
                f"👤 کاربر: <code>{user_id}</code>\n"
                f"🧾 {escape(' | '.join(details))}\n"
                "علت: KYC رد شده است."
            ),
        )
        try:
            await callback.bot.send_message(
                user_id,
                "❌ <b>احراز هویت شما تأیید نشد.</b>\n"
                "سفارش/پرداخت معلق شما برای بررسی استرداد وجه به مدیریت ارجاع شد.",
            )
        except Exception:
            pass
    else:
        await _notify_review_result(callback, user_id, False)
    await group_ops_service.audit(
        actor_id=callback.from_user.id,
        action="kyc.reject",
        subject_type="user",
        subject_id=user_id,
        new_value="rejected",
    )
    await group_ops_service.send_text(
        callback.bot,
        "kyc",
        f"❌ KYC کاربر <code>{user_id}</code> توسط <code>{callback.from_user.id}</code> رد شد.",
    )
    await callback.answer("KYC رد شد")


@kyc_router.callback_query(F.data == "kycadmin:pending")
async def kyc_pending(callback: CallbackQuery):
    if callback.from_user.id not in config.SUDO_ADMINS:
        await callback.answer("غیرمجاز", show_alert=True)
        return
    pending = await kyc_service.list_pending()
    rows = []
    for item in pending[:50]:
        rows.append([
            InlineKeyboardButton(
                text=f"🟡 {item.get('full_name') or item['user_id']} · {item['user_id']}",
                callback_data=f"kycadmin:review:{item['user_id']}",
            )
        ])
    rows.append([InlineKeyboardButton(text=config.BUTTONS["back"], callback_data="ops:hub:customers")])
    title = f"🪪 <b>KYCهای در انتظار</b>\n\nتعداد: <b>{len(pending)}</b>"
    keyboard = InlineKeyboardMarkup(inline_keyboard=rows)
    # Telegram cannot edit a photo into a text message on Back.
    if getattr(callback.message, "photo", None):
        await callback.message.answer(title, reply_markup=keyboard)
    else:
        await callback.message.edit_text(title, reply_markup=keyboard)
    await callback.answer()


@kyc_router.callback_query(F.data.startswith("kycadmin:review:"))
async def kyc_pending_review(callback: CallbackQuery):
    if callback.from_user.id not in config.SUDO_ADMINS:
        await callback.answer("غیرمجاز", show_alert=True)
        return
    user_id = int((callback.data or "").split(":")[-1])
    profile = await kyc_service.get_profile(user_id)
    if not profile:
        await callback.answer("KYC یافت نشد.", show_alert=True)
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ تأیید", callback_data=f"kycreview:approve:{user_id}"),
        InlineKeyboardButton(text="❌ رد", callback_data=f"kycreview:reject:{user_id}"),
    ], [
        InlineKeyboardButton(text=config.BUTTONS["back"], callback_data="kycadmin:pending")
    ]])
    await callback.bot.send_photo(
        callback.from_user.id,
        profile["document_file_id"],
        caption=(
            f"🪪 <b>بررسی {'کارت بانکی' if int(profile.get('verification_level') or 2) == 1 else 'مدرک هویتی'}</b>\n"
            f"🆔 Telegram ID: <code>{user_id}</code>\n"
            f"👤 نام: <b>{escape(str(profile.get('full_name') or '-'))}</b>\n"
            + (
                f"💳 کارت: <code>{escape(str(profile.get('card_first4')))} •••• •••• {escape(str(profile.get('card_last4')))}</code>\n"
                if profile.get("card_first4") and profile.get("card_last4") else ""
            )
            + f"📌 وضعیت: <b>{_user_status_text(profile)}</b>\n"
            "⚠️ در عکس کارت، ۸ رقم وسط، CVV2 و تاریخ انقضا باید پوشیده باشند."
        ),
        reply_markup=kb,
    )
    await callback.answer()


@kyc_router.callback_query(F.data.in_({"kycadmin:manual", "kycadmin:manualrevoke"}))
async def manual_trust_begin(callback: CallbackQuery, state: FSMContext):
    """Only owner accounts can authorize a past buyer by numeric Telegram ID."""
    if int(callback.from_user.id) not in config.SUDO_ADMINS:
        await callback.answer("این بخش فقط برای مالک ربات است.", show_alert=True)
        return
    revoke = str(callback.data) == "kycadmin:manualrevoke"
    await state.clear()
    await state.set_state(
        ManualTrustStates.revoke_telegram_user_id if revoke else ManualTrustStates.telegram_user_id
    )
    await callback.message.edit_text(
        "↩️ <b>لغو تأیید دستی کاربر</b>\n\n"
        "آیدی عددی تلگرام کاربری که باید دوباره مراحل عادی را طی کند بفرست."
        if revoke else
        "✅ <b>تأیید مشتری قدیمی</b>\n\n"
        "آیدی عددی تلگرام مشتری قابل‌اعتماد را ارسال کن.\n"
        "او بدون ارسال مجدد عکس کارت به مرحله پرداخت می‌رسد؛ "
        "<b>بررسی و تأیید رسید مالی همچنان اجباری است.</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="↩️ بازگشت", callback_data="ops:hub:customers")
        ]]),
    )
    await callback.answer()


@kyc_router.message(ManualTrustStates.telegram_user_id, F.text)
@kyc_router.message(ManualTrustStates.revoke_telegram_user_id, F.text)
async def manual_trust_save(message: Message, state: FSMContext):
    if int(message.from_user.id) not in config.SUDO_ADMINS:
        return
    raw = str(message.text or "").strip()
    if not raw.isascii() or not raw.isdigit() or int(raw) <= 0:
        await message.answer("⚠️ آیدی عددی تلگرام را با ارقام انگلیسی و بدون علامت بفرست.")
        return
    user_id = int(raw)
    revoke = await state.get_state() == ManualTrustStates.revoke_telegram_user_id.state
    await trust_service.set_manual_trusted(user_id, not revoke, int(message.from_user.id))
    await group_ops_service.audit(
        actor_id=int(message.from_user.id),
        action="kyc.manual_trust.revoke" if revoke else "kyc.manual_trust.approve",
        subject_type="telegram_user",
        subject_id=user_id,
        new_value={"manual_trusted": not revoke},
    )
    await state.clear()
    await message.answer(
        f"{'↩️ تأیید دستی برداشته شد' if revoke else '✅ مشتری قدیمی تأیید شد'}\n"
        f"🆔 Telegram ID: <code>{user_id}</code>\n"
        "وضعیت KYC فقط برای همین شناسه عددی تغییر کرد. "
        "قوانین بررسی پرداخت همچنان برقرار است."
    )
    if not revoke:
        try:
            from purchase_kyc_flow import resume_keyboard
            await message.bot.send_message(
                user_id,
                "✅ <b>حسابت توسط مدیریت تأیید شد!</b>\n"
                "از این به بعد مراحل بررسی اولیه رو تکرار نمی‌کنی.\n"
                "اگر خرید ناتمامی داری، از دکمه زیر ادامه بده.",
                reply_markup=await resume_keyboard(user_id),
            )
        except Exception:
            # A Telegram bot cannot PM an account that never started it.
            pass


@kyc_router.callback_query(F.data.startswith("kycadmin:toggle:"))
async def kyc_admin_toggle(callback: CallbackQuery):
    if callback.from_user.id not in config.SUDO_ADMINS:
        await callback.answer("غیرمجاز", show_alert=True)
        return
    admin_id = int((callback.data or "").split(":")[-1])
    admin = await db.get_admin_by_id(admin_id)
    if not admin:
        await callback.answer("پنل یافت نشد.", show_alert=True)
        return
    current = await kyc_service.is_required(admin_id)
    await kyc_service.set_required(admin_id, not current)
    new_text = "اجباری" if not current else "معاف"
    await callback.message.edit_text(
        f"✅ سیاست KYC پنل تغییر کرد.\n\nوضعیت جدید: <b>{new_text}</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 بازگشت به پنل", callback_data=f"manage_panel_{admin_id}")],
            [InlineKeyboardButton(text=config.BUTTONS["back"], callback_data="ops:hub:panels")],
        ]),
    )
    await callback.answer()
