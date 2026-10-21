from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram.dispatcher.middlewares.base import BaseMiddleware
from aiogram.types import Message

import config
from database import db
from group_ops_service import group_ops_service
from kyc_service import kyc_service
from trust_service import trust_service


class GroupOpsErrorMiddleware(BaseMiddleware):
    """Global Group Ops observer + unhandled-error mirror.

    /start telemetry is owned here instead of individual routers so a new user is
    recorded regardless of which start handler ultimately consumes the update.
    """

    async def _log_start(self, event: Any, data: dict[str, Any]) -> None:
        if not isinstance(event, Message):
            return
        text = str(getattr(event, "text", "") or "").strip()
        if not text.startswith("/start"):
            return
        user = getattr(event, "from_user", None)
        bot = data.get("bot")
        if not user or bot is None:
            return

        # One Telegram message must produce at most one operations event even if
        # multiple routers/middlewares inspect it.
        event_key = f"start:{int(user.id)}:{int(getattr(event, 'message_id', 0) or 0)}"
        if not await group_ops_service.claim_event(event_key):
            return

        active = []
        try:
            admins = await db.get_admins_for_user(int(user.id))
            active = [item for item in admins if bool(getattr(item, "is_active", False))]
        except Exception:
            pass

        if int(user.id) in config.SUDO_ADMINS:
            account_kind = "سودو ادمین"
        elif active:
            account_kind = f"ادمین Rebecca · {len(active)} پنل"
        else:
            account_kind = "مشتری"

        kyc_status = "unknown"
        try:
            profile = await kyc_service.get_profile(int(user.id))
            kyc_status = str((profile or {}).get("status") or "not_submitted")
        except Exception:
            pass

        trusted = False
        try:
            trusted = await trust_service.is_manually_trusted(int(user.id))
        except Exception:
            pass

        await group_ops_service.notify_start(
            bot,
            user,
            account_kind=account_kind,
            kyc_status=kyc_status,
            trusted=trusted,
        )

    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: dict[str, Any],
    ) -> Any:
        try:
            try:
                return await handler(event, data)
            finally:
                try:
                    await self._log_start(event, data)
                except Exception:
                    # Telemetry must never break the user's /start path.
                    pass
        except Exception as exc:
            bot = data.get("bot")
            if bot is not None:
                try:
                    update_id = getattr(event, "update_id", None)
                    message_id = getattr(event, "message_id", None)
                    context_id = update_id if update_id is not None else message_id
                    callback_data = getattr(event, "data", None)
                    text_value = getattr(event, "text", None)
                    route_hint = (
                        f"callback={callback_data}"
                        if callback_data
                        else (f"text={str(text_value)[:120]}" if text_value else None)
                    )
                    parts = []
                    if context_id is not None:
                        parts.append(f"update={context_id}")
                    if route_hint:
                        parts.append(route_hint)
                    await group_ops_service.report_error(
                        bot,
                        exc,
                        context=" | ".join(parts) or None,
                    )
                except Exception:
                    pass
            raise
