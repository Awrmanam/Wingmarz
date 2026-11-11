from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import CommandStart, Filter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

import config
from database import db
from handlers.home_keyboards import public_home_keyboard, reseller_home_keyboard
from kyc_service import kyc_service
from trust_service import trust_service


home_navigation_router = Router(name="home_navigation")


async def has_admin_account(user_id: int) -> bool:
    """Return True when the Telegram user owns any panel record, active or not.

    Home/navigation identity is intentionally broader than operational authorization:
    an expired or disabled reseller still needs the reseller home in order to renew,
    inspect the account, or reactivate it.
    """
    try:
        return bool(await db.get_admins_for_user(int(user_id)))
    except Exception:
        return False


class RegularAdminHome(Filter):
    async def __call__(self, message: Message) -> bool:
        user_id = int(message.from_user.id)
        if user_id in config.SUDO_ADMINS:
            return False
        return await has_admin_account(user_id)


class PublicHome(Filter):
    async def __call__(self, message: Message) -> bool:
        user_id = int(message.from_user.id)
        if user_id in config.SUDO_ADMINS:
            return False
        return not await has_admin_account(user_id)


def _join_home_sections(*keys: str) -> str:
    """Compose independently editable home blocks without forcing empty gaps."""
    parts = [str(config.MESSAGES.get(key, "")).strip() for key in keys]
    return "\n\n".join(part for part in parts if part)


async def _admin_home_text(user_id: int) -> str:
    admins = await db.get_admins_for_user(int(user_id))
    active_count = sum(1 for admin in admins if bool(getattr(admin, "is_active", False)))
    text = _join_home_sections(
        "home_reseller_title",
        "home_reseller_body",
        "home_reseller_footer",
    )
    if not text:
        text = str(config.MESSAGES.get("welcome_admin", "خوش آمدید."))
    if active_count > 1:
        text += f"\n\n🔹 شما {active_count} پنل فعال دارید."
    elif admins and active_count == 0:
        text += "\n\n⚠️ در حال حاضر پنل فعالی ندارید؛ از تمدید/افزایش برای فعال‌سازی مجدد استفاده کنید."
    return text


def _public_home_text() -> str:
    text = _join_home_sections(
        "home_public_title",
        "home_public_body",
        "home_public_footer",
    )
    return text or str(config.MESSAGES.get("customer_home", "خوش آمدید."))


async def _needs_kyc_onboarding(user_id: int, admins) -> bool:
    if int(user_id) in config.SUDO_ADMINS:
        return False
    if await trust_service.is_identity_approved(int(user_id)):
        return False
    active = [admin for admin in admins if getattr(admin, "is_active", False)]
    if admins and not active:
        # Expired/disabled resellers must still see their renewal controls.
        # KYC remains enforced when protected panel actions are attempted.
        return False
    if not active:
        # Unverified shoppers should see KYC onboarding before the storefront,
        # but can browse plans without surrendering personal documents.
        return True
    requirements = [await kyc_service.is_required(int(admin.id)) for admin in active]
    return all(requirements)


async def render_home(message: Message, user_id: int, *, edit: bool) -> None:
    """Canonical Telegram-ID-based home, with explicit KYC onboarding."""
    user_id = int(user_id)
    if user_id in config.SUDO_ADMINS:
        from handlers.operations import _edit_dashboard, _send_dashboard
        if edit:
            await _edit_dashboard(message)
        else:
            await _send_dashboard(message)
        return

    # Purchase-first UX: /start must not demand identity documents.
    # Show products and account navigation first; the shared checkout guard
    # requests KYC immediately before presenting bank transfer details.
    admins = await db.get_admins_for_user(user_id)
    if admins:
        text = await _admin_home_text(user_id)
        keyboard = await reseller_home_keyboard()
        if edit:
            await message.edit_text(text, reply_markup=keyboard)
        else:
            await message.answer(text, reply_markup=keyboard)
        return

    text = _public_home_text()
    keyboard = await public_home_keyboard()
    if edit:
        await message.edit_text(text, reply_markup=keyboard)
    else:
        await message.answer(text, reply_markup=keyboard)


@home_navigation_router.message(CommandStart(), F.chat.type == "private")
async def role_aware_start(message: Message, state: FSMContext):
    """Single /start owner after sudo: sync Rebecca by numeric Telegram ID first."""
    await state.clear()
    user_id = int(message.from_user.id)
    if user_id not in config.SUDO_ADMINS and config.PANEL_PROVIDER == "rebecca":
        try:
            from rebecca_identity_link import sync_rebecca_admins_for_telegram
            await sync_rebecca_admins_for_telegram(
                user_id,
                telegram_username=message.from_user.username,
                first_name=message.from_user.first_name,
                last_name=message.from_user.last_name,
            )
        except Exception:
            # Transient Rebecca failures must not prevent the local home/KYC.
            import logging
            logging.getLogger(__name__).exception(
                "Rebecca auto-link failed for Telegram ID %s", user_id
            )
    await render_home(message, user_id, edit=False)


@home_navigation_router.callback_query(
    F.data.in_({"back_to_admin_main", "public_back_main", "svcmarket:home"})
)
async def role_aware_back(callback: CallbackQuery, state: FSMContext):
    """All home/back aliases converge on the same role-aware home screen."""
    await state.clear()
    await render_home(callback.message, callback.from_user.id, edit=True)
    await callback.answer()
