"""Canonical Telegram entry point for Rebecca reseller management.

This composes existing, tested identity/limit/order workflows rather than
forking a second implementation of their mutations or ownership checks.
"""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import config
from database import db
from rebecca_api import RebeccaAPIError, rebecca_api

logger = logging.getLogger(__name__)
reseller_management_router = Router(name="reseller_management_center")


def _staff(user_id: int) -> bool:
    # Creating/binding provider admins is a privileged, financial operation.
    return int(user_id) in config.SUDO_ADMINS


def _keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="👥 مشاهده و جستجوی نمایندگان", callback_data="gops:admins:0")],
        [InlineKeyboardButton(text="🔎 کشف نمایندگان Rebecca", callback_data="auto_import_admins")],
        [InlineKeyboardButton(text="➕ ساخت نماینده جدید", callback_data="add_admin")],
        [InlineKeyboardButton(text="📋 سفارش‌ها و پرداخت‌های نمایندگی", callback_data="cc:orders:submitted:0")],
        [InlineKeyboardButton(text="📊 گزارش‌ها و وضعیت", callback_data="admin_status")],
        [InlineKeyboardButton(text="🪪 احراز هویت و دسترسی", callback_data="kycadmin:pending")],
    ]
    if config.PANEL_PROVIDER == "rebecca":
        rows.extend([
            [InlineKeyboardButton(text="🔒 بازبینی و تعلیق مالی PAYG", callback_data="paygaudit:root")],
            [InlineKeyboardButton(text="🔌 سرویس‌های قابل‌فروش", callback_data="rebecca_services")],
        ])
    rows.append([InlineKeyboardButton(text="🏠 بازگشت به داشبورد", callback_data="back_to_main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def render_reseller_center(message: Message) -> None:
    local = await db.get_all_admins()
    count = len(local)
    active = sum(bool(getattr(a, "is_active", False)) for a in local)
    owners = len({int(a.user_id) for a in local if int(a.user_id) > 0})
    status = "در این نصب غیرفعال است"
    if config.PANEL_PROVIDER == "rebecca":
        try:
            remote = await rebecca_api.list_admins()
            available = {
                str(row["username"])
                for row in remote
                if isinstance(row, dict)
                and row.get("username")
                and str(row.get("role") or "").lower() == "standard"
            }
            connected = {
                str(a.marzban_username) for a in local
                if a.marzban_username
            }
            status = (
                f"Rebecca Standard: <b>{len(available)}</b> | "
                f"متصل: <b>{len(available & connected)}</b> | "
                f"نیازمند بررسی اتصال: <b>{len(available - connected)}</b>"
            )
        except (RebeccaAPIError, ValueError, TypeError):
            logger.warning("Rebecca reseller discovery unavailable", exc_info=False)
            status = "⚠️ اتصال Rebecca ناموفق است؛ اطلاعات محلی همچنان قابل‌مشاهده است"
    await message.edit_text(
        "👥 <b>مدیریت نمایندگان</b>\n\n"
        f"نماینده‌های ثبت‌شده: <b>{count}</b> | فعال محلی: <b>{active}</b>\n"
        f"مالک‌های Telegram: <b>{owners}</b>\n"
        f"{status}\n\n"
        "عملیات هر نماینده از کارت همان نماینده انجام می‌شود. "
        "اطلاعات زنده از Rebecca خوانده می‌شود و سفارش و مجوز از دیتابیس Wingmarz؛ "
        "شکست API به معنی اتمام حجم یا تعلیق نیست.",
        reply_markup=_keyboard(),
    )


@reseller_management_router.message(Command("resellers"))
async def reseller_center_command(message: Message) -> None:
    if not message.from_user or not _staff(message.from_user.id):
        return
    sent = await message.answer("👥 مدیریت نمایندگان")
    await render_reseller_center(sent)


@reseller_management_router.callback_query(F.data == "resellercenter:home")
async def reseller_center_home(callback: CallbackQuery) -> None:
    if not _staff(callback.from_user.id):
        await callback.answer("دسترسی مجاز نیست", show_alert=True)
        return
    await render_reseller_center(callback.message)
    await callback.answer()
