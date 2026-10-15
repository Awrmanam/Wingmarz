from __future__ import annotations

import re
from typing import Any, Awaitable, Callable

from aiogram.dispatcher.middlewares.base import BaseMiddleware
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

import config
from database import db
from kyc_service import kyc_service


_ROOT_ACTIONS = {
    "my_info": "info_panel",
    "my_report": "report_panel",
    "my_users": "users_panel",
    "reactivate_users": "reactivate_panel",
    "admin_renew": "admin_renew_panel",
}

_PANEL_PATTERNS = (
    re.compile(r"^(?:info|report|users|reactivate)_panel_(\d+)$"),
    re.compile(r"^admin_renew_panel_(\d+)$"),
    re.compile(r"^admin_renew_(?:traffic|time|users)_(\d+)$"),
    re.compile(r"^admin_renew_(?:traffic|time|users)_amount_(\d+)_\d+$"),
    re.compile(r"^admin_full_renew_(\d+)$"),
    re.compile(r"^admin_full_renew_plan_(\d+)_\d+$"),
)


def _target_admin_id(callback_data: str) -> int | None:
    for pattern in _PANEL_PATTERNS:
        match = pattern.match(callback_data)
        if match:
            return int(match.group(1))
    return None


async def _kyc_gate_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🪪 شروع/ارسال مجدد احراز هویت", callback_data="kyc:start")],
        [InlineKeyboardButton(text="📋 وضعیت احراز هویت", callback_data="kyc:status")],
        [InlineKeyboardButton(text=config.BUTTONS["back"], callback_data="back_to_admin_main")],
    ])


class KycGateMiddleware(BaseMiddleware):
    """Block sensitive admin-panel callbacks until required KYC is approved."""

    async def __call__(
        self,
        handler: Callable[[CallbackQuery, dict[str, Any]], Awaitable[Any]],
        event: CallbackQuery,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, CallbackQuery):
            return await handler(event, data)
        user = event.from_user
        if not user or user.id in config.SUDO_ADMINS:
            return await handler(event, data)

        callback_data = str(event.data or "")
        if callback_data.startswith(("kyc:", "kycreview:", "kycadmin:")):
            return await handler(event, data)

        if await kyc_service.is_approved(user.id):
            return await handler(event, data)

        target_id = _target_admin_id(callback_data)
        if target_id is not None:
            admin = await db.get_admin_by_id(target_id)
            if not admin or int(admin.user_id) != int(user.id):
                return await handler(event, data)
            if await kyc_service.is_required(target_id):
                await event.message.edit_text(
                    "🪪 <b>احراز هویت برای این پنل الزامی است.</b>\n\n"
                    "برای دسترسی به اطلاعات و مدیریت این پنل ابتدا KYC را ثبت کنید.",
                    reply_markup=await _kyc_gate_keyboard(),
                )
                await event.answer()
                return None
            return await handler(event, data)

        if callback_data in _ROOT_ACTIONS:
            admins = [a for a in await db.get_admins_for_user(user.id) if a.is_active]
            if not admins:
                return await handler(event, data)

            exempt = []
            required = []
            for admin in admins:
                if await kyc_service.is_required(int(admin.id)):
                    required.append(admin)
                else:
                    exempt.append(admin)

            if not required:
                return await handler(event, data)

            if not exempt:
                await event.message.edit_text(
                    "🪪 <b>برای استفاده از پنل ابتدا احراز هویت را تکمیل کنید.</b>",
                    reply_markup=await _kyc_gate_keyboard(),
                )
                await event.answer()
                return None

            # Mixed account: keep exempt panels usable while required panels stay locked.
            action_prefix = _ROOT_ACTIONS[callback_data]
            rows = []
            for admin in exempt:
                panel_name = admin.admin_name or admin.marzban_username or f"Panel {admin.id}"
                rows.append([
                    InlineKeyboardButton(
                        text=f"✅ {panel_name} · معاف از KYC",
                        callback_data=f"{action_prefix}_{admin.id}",
                    )
                ])
            rows.append([InlineKeyboardButton(text="🪪 تکمیل KYC برای سایر پنل‌ها", callback_data="kyc:start")])
            rows.append([InlineKeyboardButton(text=config.BUTTONS["back"], callback_data="back_to_admin_main")])
            await event.message.edit_text(
                "بعضی از پنل‌های شما نیاز به KYC دارند.\n"
                "تا زمان تأیید، فقط پنل‌های معاف در دسترس‌اند:",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
            )
            await event.answer()
            return None

        return await handler(event, data)
