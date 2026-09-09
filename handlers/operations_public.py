from aiogram import Router
from aiogram.filters import CommandStart, Filter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

import config
from database import db
from handlers.home_navigation import render_home


operations_public_router = Router(name="operations_public")


class PublicOnly(Filter):
    async def __call__(self, message: Message) -> bool:
        if message.from_user.id in config.SUDO_ADMINS:
            return False
        # A historical/expired panel owner still belongs to the reseller home.
        try:
            return not bool(await db.get_admins_for_user(int(message.from_user.id)))
        except Exception:
            return not await db.is_admin_authorized(message.from_user.id)


@operations_public_router.message(CommandStart(), PublicOnly())
async def public_start_with_trials(message: Message, state: FSMContext):
    await state.clear()
    await render_home(message, message.from_user.id, edit=False)
