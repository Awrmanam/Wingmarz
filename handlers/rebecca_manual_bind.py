"""Owner-only Telegram UX for safe legacy Rebecca administrator binding."""
from __future__ import annotations

from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from authorization import is_owner
from rebecca_admin_binding import (
    RebeccaBindingError, bind_existing_admin, preview_binding,
)


rebecca_manual_bind_router = Router(name="rebecca_manual_bind")


class BindStates(StatesGroup):
    awaiting_telegram_id = State()
    confirming = State()


def _back_menu():
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="↩️ بازگشت به کشف", callback_data="auto_import_admins")
    ]])


async def start_legacy_bind(callback: CallbackQuery, state: FSMContext, candidate: dict) -> None:
    if not is_owner(callback.from_user.id):
        await callback.answer("اتصال نماینده موجود فقط برای مالک اصلی مجاز است.", show_alert=True)
        return
    username = str(candidate.get("username") or "").strip()
    if not username or candidate.get("role") != "standard":
        await callback.answer("فقط ادمین استاندارد Rebecca قابل اتصال است.", show_alert=True)
        return
    await state.clear()
    await state.update_data(rb_bind_username=username)
    await state.set_state(BindStates.awaiting_telegram_id)
    await callback.message.edit_text(
        "🔗 <b>اتصال ادمین قدیمی Rebecca</b>\n\n"
        f"پنل: <code>{escape(username)}</code>\n"
        "🆔 آیدی عددی تلگرام صاحب واقعی پنل را ارسال کنید.\n\n"
        "این عملیات رمز پنل را نمی‌خواهد و مالکیت موجود را خودکار انتقال نمی‌دهد.",
        reply_markup=_back_menu(),
    )
    await callback.answer()


@rebecca_manual_bind_router.message(BindStates.awaiting_telegram_id, F.text)
async def bind_numeric_id(message: Message, state: FSMContext):
    if not is_owner(message.from_user.id):
        await state.clear()
        return
    data = await state.get_data()
    username = str(data.get("rb_bind_username") or "")
    try:
        target = int((message.text or "").strip())
        details = await preview_binding(username, target)
    except (RebeccaBindingError, ValueError, TypeError) as exc:
        await message.answer(
            f"⚠️ {escape(str(exc))}\n\n"
            "اگر اختلاف مالکیت وجود دارد، این فرم برای انتقال مالکیت نیست."
        )
        return
    except Exception:
        await message.answer("⚠️ Rebecca موقتاً قابل بررسی نیست؛ اتصال انجام نشد.")
        return

    await state.update_data(rb_bind_target=target)
    await state.set_state(BindStates.confirming)
    local = details.current_local_id
    remote = details.current_remote_id
    await message.answer(
        "🔎 <b>بررسی قبل از اتصال</b>\n\n"
        f"پنل: <code>{escape(details.username)}</code>\n"
        f"🆔 صاحب انتخابی: <code>{target}</code>\n"
        f"Rebecca ID فعلی: <code>{remote if remote else '-'}</code>\n"
        f"Wingmarz ID فعلی: <code>{local if local else '-'}</code>\n"
        f"وضعیت: {escape(details.status)}\n\n"
        "با تأیید، ابتدا Rebecca بررسی/به‌روزرسانی می‌شود، سپس همان پنل "
        "به Telegram ID انتخابی در Wingmarz متصل خواهد شد.\n"
        "در صورت اختلاف مالک، عملیات متوقف می‌شود.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ تأیید اتصال", callback_data="rb:bind:confirm")],
            [InlineKeyboardButton(text="❌ انصراف", callback_data="rb:bind:cancel")],
        ]),
    )


@rebecca_manual_bind_router.callback_query(F.data == "rb:bind:confirm")
async def confirm_legacy_bind(callback: CallbackQuery, state: FSMContext):
    if not is_owner(callback.from_user.id):
        await callback.answer("فقط مالک اصلی مجاز است.", show_alert=True)
        return
    if await state.get_state() != BindStates.confirming.state:
        await callback.answer("این تأییدیه منقضی شده است.", show_alert=True)
        return
    data = await state.get_data()
    username = str(data.get("rb_bind_username") or "")
    target = int(data.get("rb_bind_target") or 0)
    if not username or target <= 0:
        await callback.answer("درخواست اتصال ناقص است.", show_alert=True)
        return
    try:
        admin_id = await bind_existing_admin(
            username, target, actor_id=int(callback.from_user.id)
        )
    except RebeccaBindingError as exc:
        await callback.answer(str(exc)[:180], show_alert=True)
        return
    except Exception:
        await callback.answer(
            "نتیجه اتصال نامشخص است؛ ابتدا وضعیت Rebecca و Wingmarz را بررسی کنید.",
            show_alert=True,
        )
        return

    await state.clear()
    await callback.message.edit_text(
        "✅ <b>نماینده به حساب عددی تلگرام متصل شد</b>\n\n"
        f"پنل: <code>{escape(username)}</code>\n"
        f"Telegram ID: <code>{target}</code>\n"
        f"Wingmarz Admin ID: <code>{admin_id}</code>\n\n"
        "مالک پنل از این به بعد با /start می‌تواند به بخش مدیریت خودش برود.",
        reply_markup=_back_menu(),
    )
    await callback.answer()


@rebecca_manual_bind_router.callback_query(F.data == "rb:bind:cancel")
async def cancel_legacy_bind(callback: CallbackQuery, state: FSMContext):
    if not is_owner(callback.from_user.id):
        await callback.answer("غیرمجاز", show_alert=True)
        return
    await state.clear()
    await callback.message.edit_text(
        "اتصال نماینده لغو شد؛ هیچ تغییری در Rebecca انجام نشد.",
        reply_markup=_back_menu(),
    )
    await callback.answer()
