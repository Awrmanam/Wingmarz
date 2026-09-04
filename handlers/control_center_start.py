from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

import config
from .control_center import _ensure_schema, _send_dashboard


control_center_start_router = Router(name="control_center_start")


@control_center_start_router.message(CommandStart(), F.from_user.id.in_(config.SUDO_ADMINS))
async def sudo_start_control_center(message: Message, state: FSMContext):
    """Open the unified dashboard for SUDO users without intercepting public/admin /start."""
    await state.clear()
    await _ensure_schema()
    await _send_dashboard(message)
