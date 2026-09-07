from aiogram import Router
from aiogram.filters import CommandStart, Filter
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, Message

import config
from database import db
from style_engine import style_engine


operations_public_router = Router(name="operations_public")


class PublicOnly(Filter):
    async def __call__(self, message: Message) -> bool:
        if message.from_user.id in config.SUDO_ADMINS:
            return False
        return not await db.is_admin_authorized(message.from_user.id)


async def _public_keyboard() -> InlineKeyboardMarkup:
    from handlers.public_handlers import get_public_main_keyboard

    return get_public_main_keyboard()


@operations_public_router.message(CommandStart(), PublicOnly())
async def public_start_with_trials(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(config.MESSAGES["customer_home"], reply_markup=await _public_keyboard())
