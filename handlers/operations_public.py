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

    base = get_public_main_keyboard()
    rows = [list(row) for row in base.inline_keyboard]
    rows.insert(1, [
        await style_engine.styled_button(
            "دریافت تست پنل",
            icon_key="panel",
            fallback="🧩",
            callback_data="ops:paneltrial:request",
        ),
        await style_engine.styled_button(
            "تست رایگان کانفیگ",
            icon_key="test",
            fallback="🧪",
            callback_data="ops:configtrial:request",
        ),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@operations_public_router.message(CommandStart(), PublicOnly())
async def public_start_with_trials(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("به ربات خوش آمدید!", reply_markup=await _public_keyboard())
