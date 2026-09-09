from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import CommandStart, Filter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

import config
from database import db


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


async def render_home(message: Message, user_id: int, *, edit: bool) -> None:
    user_id = int(user_id)
    if user_id in config.SUDO_ADMINS:
        from handlers.operations import _edit_dashboard, _send_dashboard

        if edit:
            await _edit_dashboard(message)
        else:
            await _send_dashboard(message)
        return

    if await has_admin_account(user_id):
        from handlers.admin_handlers import get_admin_keyboard

        text = await _admin_home_text(user_id)
        if edit:
            await message.edit_text(text, reply_markup=get_admin_keyboard())
        else:
            await message.answer(text, reply_markup=get_admin_keyboard())
        return

    from handlers.public_handlers import get_public_main_keyboard

    text = _public_home_text()
    if edit:
        await message.edit_text(text, reply_markup=get_public_main_keyboard())
    else:
        await message.answer(text, reply_markup=get_public_main_keyboard())


@home_navigation_router.message(CommandStart(), RegularAdminHome())
async def regular_admin_start(message: Message, state: FSMContext):
    await state.clear()
    await render_home(message, message.from_user.id, edit=False)


@home_navigation_router.callback_query(
    F.data.in_({"back_to_admin_main", "public_back_main", "svcmarket:home"})
)
async def role_aware_back(callback: CallbackQuery, state: FSMContext):
    """All home/back aliases converge on the same role-aware home screen."""
    await state.clear()
    await render_home(callback.message, callback.from_user.id, edit=True)
    await callback.answer()
