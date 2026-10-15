from __future__ import annotations

from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import config
from database import db
from kyc_service import kyc_service


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


async def _status_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🪪 شروع/ارسال مجدد KYC", callback_data="kyc:start")],
        [InlineKeyboardButton(text=config.BUTTONS["back"], callback_data="back_to_admin_main")],
    ])


@kyc_router.callback_query(F.data == "kyc:status")
async def kyc_status(callback: CallbackQuery):
    profile = await kyc_service.get_profile(callback.from_user.id)
    lines = [
        "🪪 <b>وضعیت احراز هویت</b>",
        "",
        f"وضعیت: <b>{_user_status_text(profile)}</b>",
    ]
    if profile and profile.get("full_name"):
        lines.append(f"نام ثبت‌شده: <b>{escape(str(profile['full_name']))}</b>")
    if profile and profile.get("status") == "rejected":
        lines.append(f"دلیل رد: {escape(str(profile.get('rejection_reason') or 'مدرک قابل تأیید نبود.'))}")
    await callback.message.edit_text("\n".join(lines), reply_markup=await _status_keyboard())
    await callback.answer()


@kyc_router.callback_query(F.data == "kyc:start")
async def kyc_start(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(KycStates.waiting_full_name)
    await callback.message.edit_text(
        "🪪 <b>احراز هویت</b>\n\n"
        "نام و نام خانوادگی کامل صاحب پنل را ارسال کنید.\n\n"
        "برای حفظ حریم خصوصی، شماره مدرک به‌صورت جداگانه از شما دریافت نمی‌شود.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=config.BUTTONS["cancel"], callback_data="kyc:cancel")]
        ]),
    )
    await callback.answer()


@kyc_router.callback_query(F.data == "kyc:cancel")
async def kyc_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(
        "احراز هویت لغو شد.",
        reply_markup=await _status_keyboard(),
    )
    await callback.answer()


@kyc_router.message(KycStates.waiting_full_name, F.text)
async def kyc_full_name(message: Message, state: FSMContext):
    full_name = " ".join((message.text or "").split()).strip()
    if len(full_name) < 3 or len(full_name) > 120:
        await message.answer("نام کامل معتبر نیست. لطفاً نام و نام خانوادگی را ارسال کنید.")
        return
    await state.update_data(kyc_full_name=full_name)
    await state.set_state(KycStates.waiting_document)
    await message.answer(
        "📷 حالا <b>تصویر واضح مدرک هویتی</b> را به‌صورت Photo ارسال کنید.\n\n"
        "فایل مدرک داخل سرور Wingmarz دانلود نمی‌شود؛ فقط شناسه فایل تلگرام برای بررسی ذخیره می‌شود."
    )


@kyc_router.message(KycStates.waiting_document)
async def kyc_document(message: Message, state: FSMContext):
    if not message.photo:
        await message.answer("لطفاً تصویر مدرک را به‌صورت Photo ارسال کنید.")
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
        "✅ <b>درخواست KYC ثبت شد.</b>\n\n"
        "وضعیت: 🟡 در انتظار بررسی\n"
        "بعد از تأیید، دسترسی پنل‌های نیازمند KYC خودکار باز می‌شود."
    )

    review_kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ تأیید KYC", callback_data=f"kycreview:approve:{message.from_user.id}"),
        InlineKeyboardButton(text="❌ رد KYC", callback_data=f"kycreview:reject:{message.from_user.id}"),
    ]])
    caption = (
        "🪪 <b>KYC جدید</b>\n"
        f"Telegram ID: <code>{message.from_user.id}</code>\n"
        f"نام: <b>{escape(full_name)}</b>"
    )
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
    await _notify_review_result(callback, user_id, True)
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
    await _notify_review_result(callback, user_id, False)
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
    rows.append([InlineKeyboardButton(text=config.BUTTONS["back"], callback_data="sudo_manage_admins")])
    await callback.message.edit_text(
        f"🪪 <b>KYCهای در انتظار</b>\n\nتعداد: <b>{len(pending)}</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
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
            [InlineKeyboardButton(text=config.BUTTONS["back"], callback_data="sudo_manage_admins")],
        ]),
    )
    await callback.answer()
