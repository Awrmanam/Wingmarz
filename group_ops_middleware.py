from __future__ import annotations

import traceback
from typing import Any, Awaitable, Callable

from aiogram.dispatcher.middlewares.base import BaseMiddleware
from aiogram.types import Message

import config
from database import db
from group_ops_service import group_ops_service
from kyc_service import kyc_service
from trust_service import trust_service


class GroupOpsStartMiddleware(BaseMiddleware):
    """Mirror every private /start into Group Ops before router-specific handling."""

    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: dict[str, Any],
    ) -> Any:
        try:
            if isinstance(event, Message) and event.from_user and event.chat:
                chat_type = str(getattr(getattr(event.chat, "type", None), "value", getattr(event.chat, "type", ""))).lower()
                command = str(event.text or "").strip().split(maxsplit=1)[0].lower()
                command = command.split("@", 1)[0]
                if chat_type == "private" and command == "/start":
                    cfg = await group_ops_service.get_config()
                    if cfg and int(cfg.get("enabled") or 0):
                        user_id = int(event.from_user.id)
                        admins = await db.get_admins_for_user(user_id)
                        active_admins = [admin for admin in admins if admin.is_active]
                        if user_id in config.SUDO_ADMINS:
                            account_kind = "سودو ادمین"
                        elif active_admins:
                            account_kind = f"ادمین Rebecca · {len(active_admins)} پنل"
                        else:
                            account_kind = "مشتری"
                        try:
                            kyc_status = await kyc_service.status(user_id)
                        except Exception:
                            kyc_status = "unknown"
                        try:
                            trusted = await trust_service.is_manually_trusted(user_id)
                        except Exception:
                            trusted = False
                        await group_ops_service.notify_start(
                            data.get("bot"),
                            event.from_user,
                            account_kind=account_kind,
                            kyc_status=kyc_status,
                            trusted=trusted,
                        )
        except Exception:
            # Observability must never block /start.
            pass
        return await handler(event, data)


class GroupOpsErrorMiddleware(BaseMiddleware):
    """Mirror unhandled update failures to the configured operations group."""

    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: dict[str, Any],
    ) -> Any:
        try:
            return await handler(event, data)
        except Exception as exc:
            bot = data.get("bot")
            if bot is not None:
                try:
                    user = getattr(event, "from_user", None)
                    user_id = getattr(user, "id", None)
                    event_text = getattr(event, "data", None) or getattr(event, "text", None)
                    context_parts = [f"event={type(event).__name__}"]
                    if user_id is not None:
                        context_parts.append(f"user={user_id}")
                    if event_text:
                        context_parts.append(f"input={str(event_text)[:120]}")
                    await group_ops_service.report_error(
                        bot,
                        exc,
                        context=" · ".join(context_parts),
                        traceback_text=traceback.format_exc(),
                    )
                except Exception:
                    pass
            try:
                if hasattr(event, "answer"):
                    if hasattr(event, "data"):
                        await event.answer(
                            "یک خطای داخلی ثبت شد. دوباره تلاش کنید؛ اگر تکرار شد مدیریت گزارش خطا را می‌بیند.",
                            show_alert=True,
                        )
                    else:
                        await event.answer(
                            "⚠️ یک خطای داخلی رخ داد و برای مدیریت ثبت شد. لطفاً دوباره تلاش کنید."
                        )
            except Exception:
                pass
            raise
