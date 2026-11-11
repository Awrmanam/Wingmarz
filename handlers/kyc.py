from __future__ import annotations

from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
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
    waiting_document = State()


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
    rows = []
    if not profile or profile.get("status") in {None, "rejected"}:
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
    if profile and profile.get("status") == "rejected":
        lines.append(f"دلیل رد: {escape(str(profile.get('rejection_reason') or 'مدرک قابل تأیید نبود.'))}")
    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=await _status_keyboard(callback.from_user.id),
    )
    await callback.answer()


@kyc_router.callback_query(F.data == "kyc:start")
async def kyc_start(callback: CallbackQuery, state: FSMContext):
    # Explicit voluntary KYC is available before purchasing as well as after
    # payment. Fulfillment remains gated by order_kyc_gate until approval.
    chat_type = getattr(getattr(callback.message, "chat", None), "type", "private")
    if str(getattr(chat_type, "value", chat_type)) != "private":
        await callback.answer("مدارک هویتی را فقط در گفتگوی خصوصی ربات ارسال کنید.", show_alert=True)
        return
    if await kyc_service.is_approved(callback.from_user.id):
        await callback.answer("احراز هویت شما قبلاً تأیید شده است.", show_alert=True)
        return
    if await kyc_service.status(callback.from_user.id) == "pending":
        await callback.answer("درخواست شما در صف بررسی است. منتظر پاسخ مدیر باشید.", show_alert=True)
        return
    await state.clear()
    await state.set_state(KycStates.waiting_full_name)
    await callback.message.edit_text(
        "🪪 <b>احراز هویت · مرحله ۱ از ۳</b>\n\n"
        "👤 <b>نام و نام خانوادگی</b>\n"
        "نام واقعی مطابق مدرک هویتی خود را در یک پیام بفرستید.\n\n"
        "مثال: علی رضایی\n\n"
        "🔒 نام و تصویر فقط برای تأیید هویت توسط مدیران مجاز بررسی می‌شود.\n"
        "برای انصراف، دکمه زیر را انتخاب کنید.",
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
    await state.set_state(KycStates.waiting_document)
    await message.answer(
        "🪪 <b>احراز هویت · مرحله ۲ از ۳</b>\n\n"
        "📷 <b>ارسال تصویر مدرک</b>\n"
        "یک عکس واضح از مدرک هویتی متعلق به خودتان بفرستید.\n\n"
        "• تصویر خوانا و بدون برش بخش‌های اصلی باشد.\n"
        "• آن را در همین چت خصوصی به‌صورت <b>Photo</b> ارسال کنید.\n"
        "• از ارسال عکس دیگران یا مدارک اضافی خودداری کنید.\n\n"
        "پس از ارسال، درخواست برای بررسی مدیر ثبت می‌شود.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✖️ انصراف", callback_data="kyc:cancel")
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
    photo = message.photo[-1]
    await kyc_service.submit(
        message.from_user.id,
        full_name,
        photo.file_id,
        getattr(photo, "file_unique_id", None),
    )
    await state.clear()

    await message.answer(
        "✅ <b>احراز هویت · مرحله ۳ از ۳</b>\n\n"
        "🟡 <b>مدرک دریافت شد؛ منتظر بررسی مدیر</b>\n"
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
        "🪪 <b>KYC جدید</b>\n"
        f"Telegram ID: <code>{message.from_user.id}</code>\n"
        f"Username: {('@' + escape(message.from_user.username)) if message.from_user.username else '—'}\n"
        f"نام: <b>{escape(full_name)}</b>"
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
            await callback.bot.send_message(
                user_id,
                "✅ <b>احراز هویت شما تأیید شد.</b>\n"
                "از /start وارد حساب خود شوید.",
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
            f"🪪 <b>بررسی KYC</b>\n"
            f"Telegram ID: <code>{user_id}</code>\n"
            f"نام: <b>{escape(str(profile.get('full_name') or '-'))}</b>\n"
            f"وضعیت: <b>{_user_status_text(profile)}</b>"
        ),
        reply_markup=kb,
    )
    await callback.answer()


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
