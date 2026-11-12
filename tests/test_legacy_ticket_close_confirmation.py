"""Legacy ticket buttons must never bypass canonical confirmation/authorization."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from handlers import control_center, support_v2


def test_legacy_ticket_close_delegates_to_single_authority(monkeypatch):
    callback = SimpleNamespace(
        data="cc:ticketclose:17",
        from_user=SimpleNamespace(id=123),
        answer=AsyncMock(),
    )
    state = object()
    ask = AsyncMock(return_value=None)
    monkeypatch.setattr(support_v2, "admin_close_ask", ask)

    asyncio.run(control_center.control_center_ticket_close(callback, state))

    ask.assert_awaited_once_with(callback, state)


def test_ticket_closure_legacy_path_does_not_issue_sql_directly():
    from pathlib import Path

    source = Path("handlers/control_center.py").read_text(encoding="utf-8")
    start = source.index('async def control_center_ticket_close(')
    end = source.index('@control_center_router.message(Command("ticket"))', start)
    handler = source[start:end]
    assert "admin_close_ask(callback, state)" in handler
    assert "UPDATE support_tickets" not in handler
