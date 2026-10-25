import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from aiogram.types import Chat, Message, User

import config
from kyc_middleware import KycMessageGateMiddleware
from trust_service import trust_service


def _message(text):
    return Message(
        message_id=1,
        date=datetime.now(timezone.utc),
        chat=Chat(id=91234567, type="private"),
        from_user=User(id=91234567, is_bot=False, first_name="Test"),
        text=text,
    )


@pytest.mark.parametrize("text", [None, "", "   ", "\n\t "])
def test_empty_or_non_text_messages_bypass_kyc_command_gate(monkeypatch, text):
    monkeypatch.setattr(config, "SUDO_ADMINS", [])
    identity_check = AsyncMock(side_effect=AssertionError("KYC should not run for empty text"))
    monkeypatch.setattr(trust_service, "is_identity_approved", identity_check)

    message = _message(text)
    handler = AsyncMock(return_value="handled")
    result = asyncio.run(KycMessageGateMiddleware()(handler, message, {}))

    assert result == "handled"
    handler.assert_awaited_once_with(message, {})
    identity_check.assert_not_awaited()


def test_protected_command_with_bot_mention_still_checks_kyc(monkeypatch):
    monkeypatch.setattr(config, "SUDO_ADMINS", [])
    identity_check = AsyncMock(return_value=True)
    monkeypatch.setattr(trust_service, "is_identity_approved", identity_check)

    message = _message("/my_info@WingzmarzBot extra")
    handler = AsyncMock(return_value="handled")
    result = asyncio.run(KycMessageGateMiddleware()(handler, message, {}))

    assert result == "handled"
    identity_check.assert_awaited_once_with(91234567)
    handler.assert_awaited_once_with(message, {})
