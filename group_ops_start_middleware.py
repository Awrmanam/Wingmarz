from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram.dispatcher.middlewares.base import BaseMiddleware
from aiogram.types import Message

import config
from database import db
from group_ops_service import group_ops_service
from kyc_service import kyc_service
from trust_service import trust_service


logger = logging.getLogger(__name__)


class GroupOpsStartMiddleware(BaseMiddleware):
    """Mirror every private /start before Forced Join or route handlers can block it."""

    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: dict[str, Any],
    ) -> Any:
        try:
            if isinstance(event, Message):
                text = str(event.text or "").strip()
                user = event.from_user
                if user and (text == "/start" or text.startswith("/start ")):
                    bot = data.get("bot") or event.bot
                    if int(user.id) in config.SUDO_ADMINS:
                        account_kind = "سودو ادمین"
                    else:
                        admins = await db.get_admins_for_user(int(user.id))
                        active = [admin for admin in admins if admin.is_active]
                        account_kind = (
                            f"ادمین Rebecca · {len(active)} پنل"
                            if active else "مشتری"
                        )
                    profile = await kyc_service.get_profile(int(user.id))
                    kyc_status = str((profile or {}).get("status") or "not_submitted")
                    manual_trust = await trust_service.is_manually_trusted(int(user.id))
                    sent = await group_ops_service.notify_start(
                        bot,
                        user,
                        account_kind=account_kind,
                        kyc_status=kyc_status,
                        trusted=manual_trust,
                    )
                    if not sent:
                        logger.warning("Group Ops could not mirror /start for user %s", user.id)
        except Exception as exc:
            # Observability must never block the user-facing /start flow.
            logger.warning("GroupOpsStartMiddleware failed: %s", exc)
        return await handler(event, data)
