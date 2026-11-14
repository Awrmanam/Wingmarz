"""Unverified buyers must not bypass PAYG KYC with previous wallet credit."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from handlers import payg_offers


def run(coro):
    return asyncio.run(coro)


def callback(user_id=42):
    return SimpleNamespace(
        data="paygsvc:create:p:7",
        from_user=SimpleNamespace(id=user_id),
        message=SimpleNamespace(edit_text=AsyncMock()),
        answer=AsyncMock(),
    )


def state():
    return SimpleNamespace(clear=AsyncMock())


def test_previously_funded_but_unverified_user_cannot_create_payg(monkeypatch):
    cb = callback()
    provision = AsyncMock()
    monkeypatch.setattr(
        payg_offers.payg_kyc_gate, "requires_kyc",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(payg_offers.payg_offer_service, "provision", provision)
    st = state()

    run(payg_offers.service_payg_create(cb, st))

    provision.assert_not_awaited()
    cb.message.edit_text.assert_awaited_once()
    body = cb.message.edit_text.await_args.args[0]
    assert "احراز هویت" in body
    buttons = cb.message.edit_text.await_args.kwargs["reply_markup"].inline_keyboard
    assert {b.callback_data for row in buttons for b in row} == {
        "kyc:start", "kyc:status", "paygsvc:offer:p:7"
    }
    st.clear.assert_not_awaited()


def test_approved_customer_can_create_same_payg_account(monkeypatch):
    cb = callback()
    monkeypatch.setattr(
        payg_offers.payg_kyc_gate, "requires_kyc",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(
        payg_offers.payg_offer_service,
        "provision",
        AsyncMock(return_value={"admin_id": 9}),
    )
    monkeypatch.setattr(
        payg_offers.db, "get_admin_by_id",
        AsyncMock(return_value=SimpleNamespace(
            marzban_username="demo", marzban_password="testpass", login_url="https://example.org"
        )),
    )
    monkeypatch.setattr(
        payg_offers, "get_service_by_rebecca_id",
        AsyncMock(return_value=SimpleNamespace(display_name="Promax")),
    )
    monkeypatch.setattr(
        payg_offers, "credential_message",
        lambda *_args: "Demo credentials",
    )
    st = state()

    run(payg_offers.service_payg_create(cb, st))
    payg_offers.payg_offer_service.provision.assert_awaited_once_with(42, 7)
    st.clear.assert_awaited_once()
    assert "Demo credentials" in cb.message.edit_text.await_args.args[0]
