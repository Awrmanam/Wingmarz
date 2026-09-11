from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup

import config
from authorization import is_staff
from database import db
from premium_ui_service import premium_ui_service
from style_engine import style_engine


tariffs_router = Router(name="tariffs")


async def _button(text: str, callback_data: str, *, icon_key: str | None = None, fallback: str | None = None):
    return await style_engine.styled_button(
        text,
        callback_data=callback_data,
        icon_key=icon_key,
        fallback=fallback,
    )


async def _home_callback(user_id: int) -> str:
    if is_staff(int(user_id)):
        return "back_to_main"
    return "back_to_admin_main" if await db.is_admin_authorized(int(user_id)) else "public_back_main"


@tariffs_router.callback_query(F.data == "tariffs:home")
async def tariffs_home(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    body = str(config.MESSAGES.get(
        "tariffs_page",
        "💰 <b>تعرفه‌ها</b>\n\nمتن تعرفه‌ها را از بخش مدیریت متن‌ها تنظیم کنید.",
    ))
    body = await premium_ui_service.render_placeholders(body)
    rows = [[
        await _button(
            "⬅️ بازگشت",
            await _home_callback(callback.from_user.id),
            icon_key="back",
            fallback="⬅️",
        )
    ]]
    await callback.message.edit_text(body, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()
