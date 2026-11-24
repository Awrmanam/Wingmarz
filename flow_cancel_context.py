"""Per-update FSM context for outgoing private Telegram cancel affordances.

A contextvar avoids global user/session state and stays scoped to the current
aiogram event. It cannot leak between concurrent customers.
"""
from __future__ import annotations

from contextvars import ContextVar
from typing import Any, Awaitable, Callable

from aiogram.dispatcher.middlewares.base import BaseMiddleware

_active_state: ContextVar[Any | None] = ContextVar("wingmarz_active_fsm", default=None)


def current_fsm_state() -> Any | None:
    return _active_state.get()


class FlowCancelContextMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: dict[str, Any],
    ) -> Any:
        token=_active_state.set(data.get("state"))
        try:
            return await handler(event, data)
        finally:
            _active_state.reset(token)
