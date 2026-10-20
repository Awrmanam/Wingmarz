from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram.dispatcher.middlewares.base import BaseMiddleware

from group_ops_service import group_ops_service


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
                    update_id = getattr(event, "update_id", None)
                    callback_data = getattr(event, "data", None)
                    context_parts = []
                    if update_id is not None:
                        context_parts.append(f"update_id={update_id}")
                    if callback_data:
                        context_parts.append(f"callback={str(callback_data)[:120]}")
                    context_parts.append(f"event={type(event).__name__}")
                    await group_ops_service.report_error(
                        bot,
                        exc,
                        context=" · ".join(context_parts),
                    )
                except Exception:
                    pass
            raise
