"""One non-destructive exit and canonical navigation for all Telegram flows.

Back and cancel do not mutate payment, account or KYC records: only current
FSM input is discarded. Old callback buttons in already-sent Telegram messages
remain safe after menu redesigns.
"""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

import config

navigation_guard_router = Router(name="canonical_navigation_guard")
logger = logging.getLogger(__name__)

_HOME_ALIASES = {
    "back_to_main", "back_to_admin_main", "public_back_main",
    "svcmarket:home", "back_to_public_main", "home:root",
}
_OLD_PANEL_ROOTS = {
    "my_info", "my_report", "my_users", "admin_renew", "reactivate_users",
}


async def _return_home(message: Message, user_id: int, *, edit: bool) -> None:
    from handlers.home_navigation import render_home
    if not edit:
        await render_home(message, user_id, edit=False)
        return
    try:
        await render_home(message, user_id, edit=True)
    except Exception:
        # Photos/old messages may no longer be editable. Never strand a user.
        logger.info("Could not edit prior home message; sending fresh root", exc_info=True)
        await render_home(message, user_id, edit=False)


@navigation_guard_router.callback_query(F.data == "ui:cancel")
async def cancel_current_screen(callback: CallbackQuery, state: FSMContext) -> None:
    if str(getattr(getattr(callback, "message", None), "chat", None).type
           if getattr(callback, "message", None) and getattr(callback.message, "chat", None)
           else "") != "private":
        await callback.answer("لغو فقط در گفت‌وگوی خصوصی ربات انجام می‌شود.", show_alert=True)
        return
    await state.clear()
    await _return_home(callback.message, callback.from_user.id, edit=True)
    await callback.answer("مرحله فعلی لغو شد. پرداخت‌ها و سفارش‌های ثبت‌شده تغییری نکردند.")


@navigation_guard_router.message(Command("cancel"), F.chat.type == "private")
@navigation_guard_router.message(F.text.in_({"لغو", "❌ لغو", "انصراف"}), F.chat.type == "private")
async def cancel_current_input(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "❌ مرحله فعلی لغو شد. سفارش یا پرداخت ثبت‌شده‌ای پاک نشده است."
    )
    await _return_home(message, message.from_user.id, edit=False)


@navigation_guard_router.callback_query(F.data.in_(_HOME_ALIASES))
async def unified_legacy_home(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _return_home(callback.message, callback.from_user.id, edit=True)
    await callback.answer()


@navigation_guard_router.callback_query(F.data.in_(_OLD_PANEL_ROOTS))
async def unified_legacy_panel_roots(callback: CallbackQuery, state: FSMContext) -> None:
    """Legacy root 'my info/users/renew' links all select a service first."""
    await state.clear()
    if callback.from_user.id in config.SUDO_ADMINS:
        await _return_home(callback.message, callback.from_user.id, edit=True)
    else:
        from handlers.payg_customer_center import render_accounts
        try:
            await render_accounts(callback.message, callback.from_user.id)
        except Exception:
            logger.exception("Service hub failed, returning to canonical home")
            await _return_home(callback.message, callback.from_user.id, edit=False)
    await callback.answer()


@navigation_guard_router.callback_query(F.data.startswith("panelprov:root:")
                                         | F.data.startswith("panelprov:select:"))
async def retired_provider_picker(callback: CallbackQuery, state: FSMContext) -> None:
    """Stale Rebecca-only provider selectors cannot reopen obsolete menus."""
    if config.PANEL_PROVIDER != "rebecca":
        await callback.answer("برای انتخاب سرویس از صفحه خرید جدید استفاده کنید.", show_alert=True)
        return
    await state.clear()
    fields=str(callback.data or "").split(":")
    source=fields[2] if len(fields)>2 and fields[2] in {"a", "p"} else "p"
    from handlers.payg_offers import render_purchase_method
    await render_purchase_method(callback.message, source)
    await callback.answer()


@navigation_guard_router.callback_query(F.data.startswith("payg:history:"))
async def retired_payg_history(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    from handlers.payg_customer_center import ledger_page, _styled_markup, _b
    # Keeping this read-only landing screen avoids recreating a legacy wallet.
    await callback.message.edit_text(
        "🧾 <b>گردش مالی PAYG</b>\\n\\n"
        "گزارش کامل شارژ و مصرف از کیف پول یکپارچه در دسترس است.",
        reply_markup=await _styled_markup([
            [_b("🧾 گردش مالی", "wallet:ledger:all:0")],
            [_b("📦 سرویس‌های من", "wallet:accounts")],
            [_b("🔙 صفحه اصلی", "back_to_admin_main")],
        ]),
    )
    await callback.answer()
