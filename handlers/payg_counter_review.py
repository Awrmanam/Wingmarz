"""Telegram operator interface for explicit, audited PAYG counter decisions."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import config
from payg_service import PaygError, payg_service

payg_counter_review_router = Router(name="payg_counter_review")


def _allowed(user_id: int) -> bool:
    return int(user_id) in config.SUDO_ADMINS


def _menu(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=title, callback_data=callback)
         for title, callback in row]
        for row in rows
    ])


async def _show_queue(message: Message) -> None:
    items = await payg_service.list_counter_reviews(limit=30)
    rows = [
        [(f"🔎 بررسی #{int(item['id'])} · حساب {int(item['account_id'])}",
          f"paygaudit:item:{int(item['id'])}")]
        for item in items
    ]
    rows.append([("◀️ بازگشت به مالی", "sudo_menu_sales")])
    text = (
        "🧾 <b>بازبینی شمارنده‌های PAYG</b>\n\n"
        "کاهش مصرف Rebecca تا تأیید دستی هیچ هزینه جدیدی ایجاد نمی‌کند.\n"
        f"پرونده‌های در انتظار: <b>{len(items)}</b>\n\n"
        "برای تصمیم‌گیری، مقدار زنده مجدداً از Rebecca بررسی می‌شود."
    )
    await message.edit_text(text, reply_markup=_menu(rows))


async def _show_item(message: Message, review_id: int, *, confirm: bool = False) -> None:
    items = await payg_service.list_counter_reviews(limit=100)
    item = next((row for row in items if int(row["id"]) == review_id), None)
    if item is None:
        await message.edit_text(
            "این بررسی قبلاً رسیدگی شده یا دیگر در صف نیست.",
            reply_markup=_menu([[("بازگشت", "paygaudit:root")]]),
        )
        return
    text = (
        f"🧾 <b>بررسی شمارنده #{review_id}</b>\n"
        f"حساب: <code>{int(item['account_id'])}</code>\n"
        f"آخرین شمارنده معتبر: <code>{int(item['previous_raw_bytes'])}</code> بایت\n"
        f"نمونه کاهش‌یافته: <code>{int(item['observed_raw_bytes'])}</code> بایت\n\n"
        "⚠️ تنها در صورتی ریست را تأیید کنید که علت آن را بررسی کرده‌اید. "
        "در تأیید ریست، نمونه زنده به‌عنوان مبنای دوره جدید قرار می‌گیرد؛ "
        "داده مبهم قبض نمی‌شود."
    )
    if confirm:
        text += "\n\n<b>تأیید نهایی:</b> این تصمیم در تاریخچه مالی ثبت می‌شود."
        rows = [
            [("✅ تأیید ریست و دوره جدید", f"paygaudit:applyreset:{review_id}")],
            [("↩️ انصراف", f"paygaudit:item:{review_id}")],
        ]
    else:
        rows = [
            [("♻️ بررسی و تأیید ریست", f"paygaudit:confirm:{review_id}")],
            [("✅ رد هشدار پس از بازیابی شمارنده", f"paygaudit:dismiss:{review_id}")],
            [("↩️ بازگشت", "paygaudit:root")],
        ]
    await message.edit_text(text, reply_markup=_menu(rows))


@payg_counter_review_router.message(Command("payg_audit"))
async def audit_command(message: Message) -> None:
    if not message.from_user or not _allowed(message.from_user.id):
        return
    sent = await message.answer("🧾 در حال خواندن صف بررسی PAYG ...")
    await _show_queue(sent)


@payg_counter_review_router.callback_query(F.data == "paygaudit:root")
async def audit_root(callback: CallbackQuery) -> None:
    if not _allowed(callback.from_user.id):
        await callback.answer("دسترسی مجاز نیست", show_alert=True)
        return
    await _show_queue(callback.message)
    await callback.answer()


@payg_counter_review_router.callback_query(F.data.startswith("paygaudit:item:"))
async def audit_item(callback: CallbackQuery) -> None:
    if not _allowed(callback.from_user.id):
        await callback.answer("دسترسی مجاز نیست", show_alert=True)
        return
    try:
        review_id = int(callback.data.rsplit(":", 1)[-1])
    except (TypeError, ValueError):
        await callback.answer("شناسه نامعتبر", show_alert=True)
        return
    await _show_item(callback.message, review_id)
    await callback.answer()


@payg_counter_review_router.callback_query(F.data.startswith("paygaudit:confirm:"))
async def audit_confirm(callback: CallbackQuery) -> None:
    if not _allowed(callback.from_user.id):
        await callback.answer("دسترسی مجاز نیست", show_alert=True)
        return
    try:
        review_id = int(callback.data.rsplit(":", 1)[-1])
    except (TypeError, ValueError):
        await callback.answer("شناسه نامعتبر", show_alert=True)
        return
    await _show_item(callback.message, review_id, confirm=True)
    await callback.answer()


@payg_counter_review_router.callback_query(
    F.data.startswith("paygaudit:applyreset:")
    | F.data.startswith("paygaudit:dismiss:")
)
async def audit_resolve(callback: CallbackQuery) -> None:
    if not _allowed(callback.from_user.id):
        await callback.answer("دسترسی مجاز نیست", show_alert=True)
        return
    try:
        review_id = int(callback.data.rsplit(":", 1)[-1])
    except (TypeError, ValueError):
        await callback.answer("شناسه نامعتبر", show_alert=True)
        return
    approve = callback.data.startswith("paygaudit:applyreset:")
    try:
        result = await payg_service.resolve_counter_review(
            review_id, callback.from_user.id, approve_reset=approve,
            note="Approved in Telegram by privileged operator",
        )
    except PaygError as exc:
        await callback.answer(str(exc)[:180], show_alert=True)
        return
    except Exception:
        await callback.answer("خطا در اتصال Rebecca؛ هیچ تغییری ثبت نشد.", show_alert=True)
        return
    await callback.answer("✅ تصمیم ثبت شد")
    await callback.message.edit_text(
        f"✅ بررسی #{review_id} با نتیجه <code>{result['status']}</code> ثبت شد.",
        reply_markup=_menu([[("بازگشت به صف", "paygaudit:root")]]),
    )
