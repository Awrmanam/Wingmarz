import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from forced_join_service import (
    ForcedJoinState,
    check_membership,
    inspect_channel_access,
    normalize_chat_ref,
)
from handlers.sudo_handlers import (
    FORCED_JOIN_ADD_REQUEST_ID,
    _forced_join_picker,
)


def run(coro):
    return asyncio.run(coro)


def test_normalize_forced_join_chat_refs():
    assert normalize_chat_ref("-1001234567890") == -1001234567890
    assert normalize_chat_ref("1234567890") == -1001234567890
    assert normalize_chat_ref("@arman_channel") == "@arman_channel"
    assert normalize_chat_ref("arman_channel") == "@arman_channel"
    assert normalize_chat_ref("https://t.me/arman_channel") == "@arman_channel"
    with pytest.raises(ValueError):
        normalize_chat_ref("https://t.me/+privateInvite")


def test_membership_distinguishes_not_joined_from_api_error():
    channel = {"chat_id": "-100123", "title": "Test"}

    bot = SimpleNamespace(
        get_chat_member=AsyncMock(return_value=SimpleNamespace(status=SimpleNamespace(value="member")))
    )
    result = run(check_membership(bot, channel, 55))
    assert result.state == ForcedJoinState.JOINED

    bot.get_chat_member = AsyncMock(
        return_value=SimpleNamespace(status=SimpleNamespace(value="left"))
    )
    result = run(check_membership(bot, channel, 55))
    assert result.state == ForcedJoinState.NOT_JOINED

    bot.get_chat_member = AsyncMock(side_effect=RuntimeError("bot is not an administrator"))
    result = run(check_membership(bot, channel, 55))
    assert result.state == ForcedJoinState.ERROR
    assert "administrator" in (result.error or "")


def test_channel_inspection_requires_bot_admin_and_returns_canonical_id():
    chat = SimpleNamespace(
        id=-100555,
        type=SimpleNamespace(value="channel"),
        title="Arman Channel",
        username="arman_channel",
        invite_link=None,
    )
    bot = SimpleNamespace(
        get_chat=AsyncMock(return_value=chat),
        get_me=AsyncMock(return_value=SimpleNamespace(id=999)),
        get_chat_member=AsyncMock(
            return_value=SimpleNamespace(status=SimpleNamespace(value="administrator"))
        ),
    )
    meta = run(inspect_channel_access(bot, -100555))
    assert meta["chat_id"] == "-100555"
    assert meta["title"] == "Arman Channel"
    assert meta["invite_link"] == "https://t.me/arman_channel"

    bot.get_chat_member = AsyncMock(
        return_value=SimpleNamespace(status=SimpleNamespace(value="member"))
    )
    with pytest.raises(ValueError, match="Admin"):
        run(inspect_channel_access(bot, -100555))


def test_forced_join_channel_picker_requests_channel_where_bot_is_member():
    kb = _forced_join_picker(FORCED_JOIN_ADD_REQUEST_ID)
    request = kb.keyboard[0][0].request_chat
    assert request is not None
    assert request.request_id == FORCED_JOIN_ADD_REQUEST_ID
    assert request.chat_is_channel is True
    assert request.bot_is_member is True
