"""End-to-end-ish contract tests for payment-first UX with KYC before bank details."""
import asyncio
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.types import InlineKeyboardButton

import config
import purchase_kyc_flow as checkout
from handlers import kyc, operations, payg, payg_offers
from kyc_service import KycService
from trust_service import TrustService


def run(coro):
    return asyncio.run(coro)


def _message():
    return SimpleNamespace(edit_text=AsyncMock(), answer=AsyncMock())


def test_new_card_submission_preserves_only_last_and_first_four_and_level(tmp_path):
    dbpath = str(tmp_path / "cards.db")
    svc = KycService(dbpath)

    async def scenario():
        await svc.submit(
            42, "Ali Rezaei", "telegram-photo-file-id", "unique-photo",
            verification_level=1, card_first4="6037", card_last4="1234",
        )
        profile = await svc.get_profile(42)
        assert profile["user_id"] == 42
        assert profile["card_first4"] == "6037"
        assert profile["card_last4"] == "1234"
        assert profile["verification_level"] == 1
        assert profile["status"] == "pending"
        assert await svc.is_approved(42) is False
        assert await svc.approve(42, 999) is True
        assert await svc.is_approved(42) is True
        assert await svc.approve(42, 999) is False

    run(scenario())
    with sqlite3.connect(dbpath) as db:
        schema = {row[1] for row in db.execute("PRAGMA table_info(kyc_profiles)")}
        assert {"card_first4", "card_last4", "verification_level"}.issubset(schema)


def test_previous_approved_document_kyc_is_compatible_with_additive_migration(tmp_path):
    dbpath = str(tmp_path / "old.db")
    with sqlite3.connect(dbpath) as db:
        db.execute(
            "CREATE TABLE kyc_profiles (user_id INTEGER PRIMARY KEY, full_name TEXT NOT NULL, "
            "document_file_id TEXT NOT NULL, document_file_unique_id TEXT, "
            "status TEXT NOT NULL, rejection_reason TEXT, submitted_at TIMESTAMP, "
            "reviewed_at TIMESTAMP, reviewed_by INTEGER, updated_at TIMESTAMP)"
        )
        db.execute(
            "INSERT INTO kyc_profiles(user_id,full_name,document_file_id,status) "
            "VALUES(42,'Old User','old-photo','approved')"
        )
    svc = KycService(dbpath)
    assert run(svc.is_approved(42)) is True
    profile = run(svc.get_profile(42))
    assert profile["verification_level"] == 2
    assert profile["card_first4"] is None


@pytest.mark.parametrize("data,expected", [
    ("6037 1234", ("6037", "1234")),
    ("۶۰۳۷ ۱۲۳۴", ("6037", "1234")),
    ("6037-1234", ("6037", "1234")),
    ("6037999900001234", None),
    ("6037 7999 0000 1234", None),
    ("123", None),
    ("abcd xxxx", None),
    ("6037-12A4", None),
])
def test_only_eight_reduced_card_digits_accepted(data, expected):
    assert kyc._card_digits(data) == expected


def test_unverified_fixed_checkout_never_reads_or_displays_payment_cards(monkeypatch):
    monkeypatch.setattr(operations.order_kyc_gate, "requires_kyc", AsyncMock(return_value=True))
    monkeypatch.setattr(checkout, "require_checkout_kyc", AsyncMock(return_value=True))
    cards = AsyncMock()
    monkeypatch.setattr(operations.db, "get_cards", cards)
    message = _message()
    run(operations.render_checkout_payment(
        message,
        {"id": 10, "user_id": 42, "status": "pending",
         "plan_name_snapshot": "VPN", "price_snapshot": 20000},
    ))
    cards.assert_not_awaited()
    checkout.require_checkout_kyc.assert_awaited_once()


@pytest.mark.parametrize("kind", ["payg", "paygsvc"])
def test_unverified_payg_initial_checkout_hides_cards(monkeypatch, kind):
    monkeypatch.setattr(checkout, "require_checkout_kyc", AsyncMock(return_value=True))
    cards = AsyncMock()
    monkeypatch.setattr(payg.db, "get_cards", cards)
    monkeypatch.setattr(payg.payg_service, "get_topup", AsyncMock(
        return_value={"id": 4, "user_id": 42, "purpose": "initial", "status": "pending"}
    ))
    monkeypatch.setattr(payg_offers.payg_offer_service, "topup_context", AsyncMock(
        return_value={"source": "p", "service_id": 8}
    ))
    monkeypatch.setattr(payg_offers, "get_service_by_rebecca_id", AsyncMock(
        return_value=SimpleNamespace(display_name="Game")
    ))
    msg = _message()
    if kind == "payg":
        run(payg._render_payment(msg, 4))
    else:
        monkeypatch.setattr(payg_offers.payg_service, "get_topup", AsyncMock(
            return_value={"id": 4, "user_id": 42, "purpose": "initial_service", "status": "pending"}
        ))
        run(payg_offers._send_payment(msg, 4, edit=True))
    cards.assert_not_awaited()


def test_checkout_context_is_isolated_by_numeric_user_and_survives_restart(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATABASE_PATH", str(tmp_path / "checkout.db"))

    async def scenario():
        await checkout.register_checkout(42, "fixed", 10, "p")
        await checkout.register_checkout(43, "payg", 7, "a")
        one = await checkout.pending_checkout(42)
        two = await checkout.pending_checkout(43)
        assert one["reference_id"] == 10 and one["kind"] == "fixed"
        assert two["reference_id"] == 7 and two["kind"] == "payg"
        await checkout.register_checkout(42, "paygsvc", 99, "p")
        assert (await checkout.pending_checkout(42))["reference_id"] == 99
        assert (await checkout.pending_checkout(43))["reference_id"] == 7

    run(scenario())


def test_guard_skips_identity_approved_and_manually_trusted_users(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATABASE_PATH", str(tmp_path / "checkout.db"))
    service = TrustService(str(tmp_path / "checkout.db"))
    monkeypatch.setattr(checkout, "trust_service", service)
    message = _message()

    async def scenario():
        await service.set_manual_trusted(42, True, 999)
        assert await checkout.require_checkout_kyc(
            message, telegram_user_id=42, kind="fixed", reference_id=18
        ) is False
        assert await checkout.pending_checkout(42) is None
        # Manual trust never turns on automatic payment approval by itself.
        assert await service.can_auto_approve(42, 18) is False
    run(scenario())
    message.edit_text.assert_not_awaited()


def test_guard_pauses_before_any_card_info_and_uses_styled_button(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATABASE_PATH", str(tmp_path / "checkout.db"))
    monkeypatch.setattr(checkout.trust_service, "is_identity_approved", AsyncMock(return_value=False))
    monkeypatch.setattr(checkout.kyc_service, "get_profile", AsyncMock(return_value=None))
    monkeypatch.setattr(checkout.style_engine, "render_emoji", AsyncMock(return_value="🪪"))
    async def button(text, **kwargs):
        return InlineKeyboardButton(text=text, callback_data=kwargs["callback_data"])
    monkeypatch.setattr(checkout.style_engine, "styled_button", button)
    message = _message()

    async def scenario():
        assert await checkout.require_checkout_kyc(
            message, telegram_user_id=42, kind="fixed", reference_id=17, source="p"
        ) is True
        assert (await checkout.pending_checkout(42))["reference_id"] == 17

    run(scenario())
    text = message.edit_text.await_args.args[0]
    assert "فقط یک قدم تا پرداخت" in text
    assert "CVV2" in text
    assert "شماره کامل کارت" in text
    actions = [
        item.callback_data
        for row in message.edit_text.await_args.kwargs["reply_markup"].inline_keyboard
        for item in row
    ]
    assert "kyc:start" in actions
    assert "kyc:level:2" in actions


def test_manual_trust_sudo_only_and_numeric_id(monkeypatch):
    monkeypatch.setattr(config, "SUDO_ADMINS", [999])
    save = AsyncMock()
    audit = AsyncMock()
    monkeypatch.setattr(kyc.trust_service, "set_manual_trusted", save)
    monkeypatch.setattr(kyc.group_ops_service, "audit", audit)
    from aiogram.fsm.state import State
    state = SimpleNamespace(
        get_state=AsyncMock(return_value=kyc.ManualTrustStates.telegram_user_id.state),
        clear=AsyncMock(),
    )
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=999),
        text="42424242",
        answer=AsyncMock(),
        bot=SimpleNamespace(send_message=AsyncMock()),
    )
    run(kyc.manual_trust_save(message, state))
    save.assert_awaited_once_with(42424242, True, 999)
    audit.assert_awaited_once()
    assert audit.await_args.kwargs["subject_id"] == 42424242

    save.reset_mock()
    message.from_user.id = 123
    run(kyc.manual_trust_save(message, state))
    save.assert_not_awaited()


def test_checkout_resume_rejects_other_users_order(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATABASE_PATH", str(tmp_path / "resume.db"))
    monkeypatch.setattr(checkout.trust_service, "is_identity_approved", AsyncMock(return_value=True))
    monkeypatch.setattr(checkout.db, "get_order_by_id", AsyncMock(
        return_value={"id": 19, "user_id": 999, "status": "pending"}
    ))
    renderer = AsyncMock()
    monkeypatch.setattr(operations, "render_checkout_payment", renderer)
    cb = SimpleNamespace(
        from_user=SimpleNamespace(id=42),
        message=_message(), answer=AsyncMock(),
    )

    async def scenario():
        await checkout.register_checkout(42, "fixed", 19, "p")
        await checkout.checkout_resume(cb)

    run(scenario())
    renderer.assert_not_awaited()
    assert cb.answer.await_args.kwargs["show_alert"] is True


def test_checkout_resume_returns_to_same_paid_record_without_new_order(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATABASE_PATH", str(tmp_path / "resume.db"))
    monkeypatch.setattr(checkout.trust_service, "is_identity_approved", AsyncMock(return_value=True))
    order = {
        "id": 19, "user_id": 42, "status": "pending",
        "price_snapshot": 12000, "plan_name_snapshot": "Gaming",
    }
    monkeypatch.setattr(checkout.db, "get_order_by_id", AsyncMock(return_value=order))
    renderer = AsyncMock()
    monkeypatch.setattr(operations, "render_checkout_payment", renderer)
    cb = SimpleNamespace(
        from_user=SimpleNamespace(id=42),
        message=_message(), answer=AsyncMock(),
    )

    async def scenario():
        await checkout.register_checkout(42, "fixed", 19, "p")
        await checkout.checkout_resume(cb)

    run(scenario())
    renderer.assert_awaited_once_with(cb.message, order, source="p")
    cb.answer.assert_awaited_once()


def test_approved_kyc_notice_includes_one_click_payment_resume(monkeypatch):
    keyboard = object()
    monkeypatch.setattr(checkout, "resume_keyboard", AsyncMock(return_value=keyboard))
    bot = SimpleNamespace(send_message=AsyncMock())
    callback = SimpleNamespace(bot=bot)
    run(kyc._notify_review_result(callback, 42, True))
    bot.send_message.assert_awaited_once()
    assert bot.send_message.await_args.args[0] == 42
    assert bot.send_message.await_args.kwargs["reply_markup"] is keyboard
    assert "تأیید شد" in bot.send_message.await_args.args[1]


def test_old_manual_trust_does_not_enable_payment_auto_approval(tmp_path):
    svc = TrustService(str(tmp_path / "trust.db"))
    async def scenario():
        await svc.set_manual_trusted(42, True, 999)
        assert await svc.is_identity_approved(42) is True
        assert await svc.auto_approve_enabled(42) is False
        assert await svc.can_auto_approve(42, 10) is False
    run(scenario())


def test_rebecca_reseller_back_button_remains_in_reseller_store(monkeypatch):
    async def button(text, **kwargs):
        return InlineKeyboardButton(text=text, callback_data=kwargs["callback_data"])
    monkeypatch.setattr(checkout.style_engine, "styled_button", button)
    keyboard = run(checkout._onboarding_keyboard(pending=False, source="a"))
    actions = [b.callback_data for row in keyboard.inline_keyboard for b in row]
    assert "admin_buy_reseller" in actions
    assert "public_buy_reseller" not in actions
