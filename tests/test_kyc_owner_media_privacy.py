"""KYC media must never fall back to Group Ops or dynamic manager chats."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from handlers import kyc


def run(coro):
    return asyncio.run(coro)


def _message():
    bot = SimpleNamespace(send_photo=AsyncMock(), send_message=AsyncMock())
    return SimpleNamespace(
        from_user=SimpleNamespace(id=42, username="buyer"),
        photo=[SimpleNamespace(file_id="telegram-photo-id", file_unique_id="unique-id")],
        answer=AsyncMock(), bot=bot,
    )


def _state():
    return SimpleNamespace(
        get_data=AsyncMock(return_value={
            "kyc_full_name": "Ali Rezaei", "kyc_level": 1,
            "kyc_first4": "6037", "kyc_last4": "1234",
        }),
        clear=AsyncMock(),
    )


def test_kyc_photo_delivered_only_in_private_owner_chat(monkeypatch):
    msg, state = _message(), _state()
    monkeypatch.setattr(kyc, "OWNER_IDS", (123,))
    monkeypatch.setattr(kyc.config, "SUDO_ADMINS", [123, 456])
    monkeypatch.setattr(kyc.kyc_service, "submit", AsyncMock())
    group_text, group_photo = AsyncMock(return_value=True), AsyncMock(
        side_effect=AssertionError("Never send KYC media to Group Ops")
    )
    monkeypatch.setattr(kyc.group_ops_service, "send_text", group_text)
    monkeypatch.setattr(kyc.group_ops_service, "send_photo", group_photo)

    run(kyc.kyc_document(msg, state))

    kyc.kyc_service.submit.assert_awaited_once()
    msg.bot.send_photo.assert_awaited_once()
    sent = msg.bot.send_photo.await_args.kwargs
    assert sent["chat_id"] == 123
    assert sent["photo"] == "telegram-photo-id"
    assert "6037" in sent["caption"]
    assert group_text.await_count == 1
    group_photo.assert_not_awaited()
    metadata = group_text.await_args.args[2]
    assert "6037" not in metadata and "1234" not in metadata
    assert "telegram-photo-id" not in metadata
    assert "42" in metadata


def test_kyc_private_photo_delivery_failure_never_sends_media_to_group(monkeypatch):
    msg, state = _message(), _state()
    msg.bot.send_photo.side_effect = PermissionError("blocked")
    monkeypatch.setattr(kyc, "OWNER_IDS", (123,))
    monkeypatch.setattr(kyc.kyc_service, "submit", AsyncMock())
    text = AsyncMock(return_value=True)
    photo = AsyncMock(side_effect=AssertionError("group photo forbidden"))
    monkeypatch.setattr(kyc.group_ops_service, "send_text", text)
    monkeypatch.setattr(kyc.group_ops_service, "send_photo", photo)

    run(kyc.kyc_document(msg, state))

    assert "ناموفق" in text.await_args.args[2]
    photo.assert_not_awaited()
    # This request is still recoverable through the pending-KYC queue.
    kyc.kyc_service.submit.assert_awaited_once()
    state.clear.assert_awaited_once()
