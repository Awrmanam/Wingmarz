"""Purchase-first KYC gate and durable checkout resumption.

Never reveal bank transfer destination cards until an identity is approved.
The pending order or top-up remains locally stored for the same Telegram ID.
"""
from __future__ import annotations

from html import escape
import aiosqlite

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

import config
from trust_service import trust_service
from kyc_service import kyc_service
from style_engine import style_engine
from database import db


checkout_kyc_router = Router(name="purchase_kyc")
_VALID_KINDS = {"fixed", "payg", "paygsvc"}


async def _db_path() -> str:
    return str(config.DATABASE_PATH)


async def _schema() -> None:
    async with aiosqlite.connect(await _db_path()) as conn:
        await conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS purchase_kyc_resume (
                telegram_user_id INTEGER PRIMARY KEY CHECK(telegram_user_id > 0),
                kind TEXT NOT NULL CHECK(kind IN ('fixed','payg','paygsvc')),
                reference_id INTEGER NOT NULL CHECK(reference_id > 0),
                source TEXT NOT NULL DEFAULT 'p',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        await conn.commit()


async def register_checkout(telegram_user_id: int, kind: str, reference_id: int, source: str = "p") -> None:
    if int(telegram_user_id) <= 0 or int(reference_id) <= 0 or kind not in _VALID_KINDS:
        raise ValueError("invalid checkout context")
    await _schema()
    async with aiosqlite.connect(await _db_path()) as conn:
        await conn.execute(
            """
            INSERT INTO purchase_kyc_resume(telegram_user_id,kind,reference_id,source)
            VALUES(?,?,?,?)
            ON CONFLICT(telegram_user_id) DO UPDATE SET
                kind=excluded.kind,reference_id=excluded.reference_id,
                source=excluded.source,created_at=CURRENT_TIMESTAMP
            """,
            (int(telegram_user_id), kind, int(reference_id), source if source in {"p", "a"} else "p"),
        )
        await conn.commit()


async def pending_checkout(telegram_user_id: int) -> dict | None:
    await _schema()
    async with aiosqlite.connect(await _db_path()) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            "SELECT * FROM purchase_kyc_resume WHERE telegram_user_id=?",
            (int(telegram_user_id),),
        ) as cur:
            row = await cur.fetchone()
        return dict(row) if row else None


async def resume_keyboard(telegram_user_id: int) -> InlineKeyboardMarkup | None:
    ctx = await pending_checkout(telegram_user_id)
    if not ctx:
        return None
    button = await style_engine.styled_button(
        "ادامه پرداخت سفارش قبلی",
        callback_data="ckyc:resume",
        icon_key="payment",
        fallback="💳",
    )
    return InlineKeyboardMarkup(inline_keyboard=[[button]])


async def _onboarding_keyboard(*, pending: bool) -> InlineKeyboardMarkup:
    rows = []
    if pending:
        rows.append([
            await style_engine.styled_button(
                "وضعیت بررسی مدارک", callback_data="kyc:status",
                icon_key="info", fallback="🔎",
            )
        ])
    else:
        rows.append([
            await style_engine.styled_button(
                "تأیید کارت و ادامه خرید", callback_data="kyc:start",
                icon_key="success", fallback="✅",
            )
        ])
        rows.append([
            await style_engine.styled_button(
                "احراز هویت پیشرفته", callback_data="kyc:level:2",
                icon_key="kyc", fallback="🪪",
            )
        ])
    rows.append([
        await style_engine.styled_button(
            "بازگشت به فروشگاه", callback_data="public_buy_reseller",
            icon_key="back", fallback="↩️",
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def approved_payment_card_note(telegram_user_id: int) -> str:
    """Show the single manually reviewed sender-card fingerprint, never a PAN."""
    profile = await kyc_service.get_profile(int(telegram_user_id))
    if (
        profile and str(profile.get("status") or "") == "approved"
        and profile.get("card_first4") and profile.get("card_last4")
    ):
        first4 = escape(str(profile["card_first4"]))
        last4 = escape(str(profile["card_last4"]))
        return (
            f"🪪 کارت مبدأ ثبت‌شده شما: <code>{first4} •••• •••• {last4}</code>\n"
            "لطفاً از همین کارت پرداخت کن؛ مدیر مشخصات کارت مبدأ رسید رو به‌صورت دستی تطبیق می‌ده."
        )
    approved = await trust_service.is_identity_approved(telegram_user_id)
    if approved:
        return (
            "✅ حسابت برای ادامه پرداخت تأیید شده است.\n"
            "لطفاً از کارت بانکی متعلق به خودت واریز کن."
        )
    return (
        "🔎 کارت مبدأ هنوز ثبت نشده؛ قبل از پرداخت باید تأییدش کنیم."
    )


async def require_checkout_kyc(
    message, *, telegram_user_id: int, kind: str, reference_id: int,
    source: str = "p", edit: bool = True,
) -> bool:
    """Return True only if checkout was paused before displaying bank cards."""
    if await trust_service.is_identity_approved(telegram_user_id):
        return False
    await register_checkout(telegram_user_id, kind, reference_id, source)
    profile = await kyc_service.get_profile(telegram_user_id)
    status = str((profile or {}).get("status") or "not_submitted")
    pending = status == "pending"
    title_icon = await style_engine.render_emoji("kyc", fallback="🪪")
    msg = (
        f"{title_icon} <b>فقط یک قدم تا پرداخت!</b>\n\n"
        "سفارشت ثبت شده و محفوظ است؛ قبل از اینکه کارت پرداخت را نشانت بدهیم، "
        "یک‌بار کارت خودت را بررسی می‌کنیم. برای خریدهای بعدی تکرار نمی‌شود. 🙂\n\n"
        "<b>مراحل کوتاه:</b>\n"
        "۱. نام و نام خانوادگی\n"
        "۲. فقط ۴ رقم اول و ۴ رقم آخر کارت خودت\n"
        "۳. عکس کارت با <b>۸ رقم وسط، CVV2 و تاریخ انقضا پوشانده‌شده</b>\n"
        "۴. تأیید مدیر و ادامه همین سفارش\n\n"
        "🔒 هرگز شماره کامل کارت، رمز، CVV2 یا کد پیامکی نفرست."
    )
    if pending:
        msg = (
            f"{title_icon} <b>مدرکت رسید، ممنون!</b>\n\n"
            "درخواستت در صف بررسی است. همین سفارش محفوظ می‌ماند و پس از "
            "تأیید می‌توانی پرداخت را ادامه بدهی. نیازی به ارسال دوباره نیست. 🌿"
        )
    kb = await _onboarding_keyboard(pending=pending)
    if edit:
        await message.edit_text(msg, reply_markup=kb)
    else:
        await message.answer(msg, reply_markup=kb)
    return True


@checkout_kyc_router.callback_query(F.data == "ckyc:resume")
async def checkout_resume(callback: CallbackQuery):
    """Re-open the *same* pending payment only for its numeric Telegram owner."""
    user_id = int(callback.from_user.id)
    ctx = await pending_checkout(user_id)
    if not ctx:
        await callback.answer("سفارش نیمه‌تمام پیدا نشد؛ از فروشگاه شروع کن.", show_alert=True)
        return
    if not await trust_service.is_identity_approved(user_id):
        await callback.answer("اول باید احراز هویتت تأیید بشه.", show_alert=True)
        return
    kind, reference_id = str(ctx["kind"]), int(ctx["reference_id"])
    if kind == "fixed":
        order = await db.get_order_by_id(reference_id)
        if not order or int(order["user_id"]) != user_id or str(order.get("status")) != "pending":
            await callback.answer("این سفارش دیگر در انتظار پرداخت نیست.", show_alert=True)
            return
        from handlers.operations import render_checkout_payment
        await render_checkout_payment(callback.message, order, source=str(ctx.get("source") or "p"))
    elif kind in {"payg", "paygsvc"}:
        from payg_service import payg_service
        topup = await payg_service.get_topup(reference_id)
        if not topup or int(topup["user_id"]) != user_id or str(topup.get("status")) != "pending":
            await callback.answer("این درخواست شارژ دیگر در انتظار پرداخت نیست.", show_alert=True)
            return
        if kind == "payg":
            from handlers.payg import _render_payment
            await _render_payment(callback.message, reference_id, source=str(ctx.get("source") or "p"))
        else:
            from handlers.payg_offers import _send_payment
            await _send_payment(callback.message, reference_id, edit=True)
    else:
        await callback.answer("مسیر پرداخت نامعتبر است.", show_alert=True)
        return
    await callback.answer()
