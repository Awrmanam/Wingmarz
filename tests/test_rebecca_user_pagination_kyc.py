"""Old Rebecca pagination buttons obey the same KYC gate as page one."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import kyc_middleware as gate


class FakeCallback:
    def __init__(self, raw: str, owner: int):
        self.data = raw
        self.from_user = SimpleNamespace(id=owner)
        self.message = SimpleNamespace(edit_text=AsyncMock())
        self.answer = AsyncMock()


def test_rebecca_pagination_targets_panel_kyc():
    assert gate._target_admin_id("rebecca_users_page:78:0") == 78
    assert gate._target_admin_id("rebecca_users_page:78:22") == 78
    assert gate._target_admin_id("rebecca_users_page:78:-1") is None
    assert gate._target_admin_id("rebecca_users_page:wrong:0") is None


@pytest.mark.parametrize("required", [True, False])
def test_rebecca_pagination_rechecks_revocable_kyc(monkeypatch, required):
    monkeypatch.setattr(gate, "CallbackQuery", FakeCallback)
    monkeypatch.setattr(gate.config, "SUDO_ADMINS", [])
    monkeypatch.setattr(
        gate.trust_service, "is_identity_approved", AsyncMock(return_value=False)
    )
    monkeypatch.setattr(
        gate.db, "get_admin_by_id",
        AsyncMock(return_value=SimpleNamespace(id=78, user_id=14)),
    )
    monkeypatch.setattr(
        gate.kyc_service, "is_required", AsyncMock(return_value=required)
    )
    event = FakeCallback("rebecca_users_page:78:1", 14)
    handler = AsyncMock(return_value="ok")
    result = asyncio.run(gate.KycGateMiddleware()(handler, event, {}))
    if required:
        assert result is None
        handler.assert_not_awaited()
        event.message.edit_text.assert_awaited_once()
        assert "احراز هویت" in event.message.edit_text.await_args.args[0]
    else:
        assert result == "ok"
        handler.assert_awaited_once_with(event, {})


def test_other_reseller_callback_not_granted_by_kyc_middleware(monkeypatch):
    monkeypatch.setattr(gate, "CallbackQuery", FakeCallback)
    monkeypatch.setattr(gate.config, "SUDO_ADMINS", [])
    monkeypatch.setattr(
        gate.trust_service, "is_identity_approved", AsyncMock(return_value=False)
    )
    monkeypatch.setattr(
        gate.db, "get_admin_by_id",
        AsyncMock(return_value=SimpleNamespace(id=77, user_id=999)),
    )
    event = FakeCallback("rebecca_users_page:77:0", 14)
    handler = AsyncMock()
    asyncio.run(gate.KycGateMiddleware()(handler, event, {}))
    # The middleware delegates ownership denials to the scoped handler,
    # rather than treating the foreign panel as the requester's own.
    handler.assert_awaited_once_with(event, {})
