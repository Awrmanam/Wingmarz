from __future__ import annotations

import traceback
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
    """Notify Group Ops once per Telegram numeric user ID (not per /start).

    /start always reaches its role-specific handler. We observe *after*
    handling so a newly auto-linked Rebecca account is classified correctly.
    An outage in reporting never blocks the user or sends duplicate events.
    """

    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: dict[str, Any],
    ) -> Any:
        result = await handler(event, data)
        first_user_id: int | None = None
        delivered = False
        try:
            if not isinstance(event, Message) or not event.from_user or not event.chat:
                return result
            if bool(getattr(event.from_user, "is_bot", False)):
                return result

            chat_type = str(
                getattr(getattr(event.chat, "type", None), "value", getattr(event.chat, "type", ""))
            ).lower()
            raw_text = str(event.text or "").strip()
            command = raw_text.split(maxsplit=1)[0].lower() if raw_text else ""
            command = command.split("@", 1)[0]
            if chat_type != "private" or command != "/start":
                return result

            user_id = int(event.from_user.id)
            if user_id <= 0:
                return result

            cfg = await group_ops_service.get_config()
            if not cfg or not int(cfg.get("enabled") or 0):
                # A disconnected reporting group must never intercept /start.
                return result

            if not await group_ops_service.record_private_start(user_id):
                # Repeated starts and username changes are silent.
                return result
            first_user_id = user_id

            # The actual /start handler may have just synchronized a Rebecca
            # account by its telegram_id; use that post-handler identity.
            try:
                admins = await db.get_admins_for_user(user_id)
                active_admins = [admin for admin in admins if admin.is_active]
            except Exception as exc:
                logger.warning("Start role lookup failed for Telegram ID %s: %s", user_id, exc)
                active_admins = []

            if user_id in config.SUDO_ADMINS:
                account_kind = "سودو ادمین"
            elif active_admins:
                provider = "Rebecca" if config.PANEL_PROVIDER == "rebecca" else "Marzban"
                account_kind = f"ادمین {provider} · {len(active_admins)} پنل"
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

            bot = data.get("bot") or getattr(event, "bot", None)
            if bot is None:
                logger.warning("Group Ops first start skipped: bot instance unavailable for %s", user_id)
            else:
                delivered = bool(await group_ops_service.notify_start(
                    bot,
                    event.from_user,
                    account_kind=account_kind,
                    kyc_status=kyc_status,
                    trusted=trusted,
                ))
                if not delivered:
                    logger.warning("Group Ops first start delivery failed for Telegram ID %s", user_id)
        except Exception:
            # Reporting must never block /start or photo/FSM messages.
            logger.exception("Group Ops first start observer failed")
        finally:
            if first_user_id is not None:
                try:
                    await group_ops_service.mark_start_delivery(first_user_id, delivered)
                except Exception:
                    logger.exception("Could not persist start report outcome for %s", first_user_id)
        return result


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
