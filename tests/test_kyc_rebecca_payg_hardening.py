"""Regression coverage for the KYC/Rebecca/PAYG production hardening."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiosqlite
import pytest

import config
from handlers import home_navigation, kyc
from payg_notification_service import PaygNotificationService
from payg_service import GIB, PaygService
import payg_service as payg_module
import rebecca_admin_ops


def run(coro):
    return asyncio.run(coro)


def _message():
    return SimpleNamespace(answer=AsyncMock(), edit_text=AsyncMock())


def test_new_public_account_is_directed_to_kyc_on_start(monkeypatch):
    monkeypatch.setattr(config, "SUDO_ADMINS", [999])
    monkeypatch.setattr(home_navigation.db, "get_admins_for_user", AsyncMock(return_value=[]))
    monkeypatch.setattr(home_navigation.trust_service, "is_identity_approved", AsyncMock(return_value=False))
    monkeypatch.setattr(home_navigation.kyc_service, "get_profile", AsyncMock(return_value=None))
    msg = _message()

    run(home_navigation.render_home(msg, 42, edit=False))

    text = msg.answer.await_args.args[0]
    markup = msg.answer.await_args.kwargs["reply_markup"]
    callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
    assert "مرحله" not in text  # Short overview, then detailed numbered stages
    assert "مراحل احراز هویت" in text
    assert "<code>42</code>" in text
    assert "kyc:start" in callbacks
    assert "public_buy_reseller" in callbacks


def test_kyc_is_required_for_unverified_existing_rebecca_admin(monkeypatch):
    monkeypatch.setattr(config, "SUDO_ADMINS", [999])
    monkeypatch.setattr(home_navigation.db, "get_admins_for_user", AsyncMock(
        return_value=[SimpleNamespace(id=12, user_id=42, is_active=True)]
    ))
    monkeypatch.setattr(home_navigation.trust_service, "is_identity_approved", AsyncMock(return_value=False))
    monkeypatch.setattr(home_navigation.kyc_service, "is_required", AsyncMock(return_value=True))
    monkeypatch.setattr(home_navigation.kyc_service, "get_profile", AsyncMock(return_value=None))
    msg = _message()

    run(home_navigation.render_home(msg, 42, edit=False))

    callbacks = [
        button.callback_data
        for row in msg.answer.await_args.kwargs["reply_markup"].inline_keyboard
        for button in row
    ]
    assert callbacks[0] == "kyc:start"
    assert "public_buy_reseller" not in callbacks


def test_rebecca_identity_is_refreshed_by_numeric_id_before_start_render(monkeypatch):
    import rebecca_identity_link
    monkeypatch.setattr(config, "SUDO_ADMINS", [999])
    monkeypatch.setattr(config, "PANEL_PROVIDER", "rebecca")
    called = []

    async def sync(user_id, **kwargs):
        called.append(("sync", user_id, kwargs["telegram_username"]))

    async def render(_message, user_id, *, edit):
        called.append(("render", user_id, edit))

    monkeypatch.setattr(rebecca_identity_link, "sync_rebecca_admins_for_telegram", sync)
    monkeypatch.setattr(home_navigation, "render_home", render)
    state = SimpleNamespace(clear=AsyncMock())
    message = SimpleNamespace(from_user=SimpleNamespace(
        id=42, username="display-only", first_name="A", last_name="B"
    ))
    run(home_navigation.role_aware_start(message, state))

    assert called == [("sync", 42, "display-only"), ("render", 42, False)]


def test_kyc_pending_does_not_offer_duplicate_submission():
    pending = {"status": "pending", "full_name": "Ali Rezaei"}
    keys = [button.callback_data for row in kyc.kyc_entry_keyboard(pending) for button in row] if False else [
        button.callback_data for row in kyc.kyc_entry_keyboard(pending).inline_keyboard for button in row
    ]
    assert "kyc:start" not in keys
    assert "kyc:status" in keys
    assert "در صف بررسی" in kyc.kyc_intro_text(pending, 42)


def test_payg_wallet_thresholds_once_per_numeric_user_and_reset(tmp_path):
    svc = PaygNotificationService(str(tmp_path / "notices.db"))

    async def scenario():
        assert await svc.due_wallet_threshold(42, 450) is None
        assert await svc.due_wallet_threshold(42, 200) == 200
        await svc.mark_wallet_notified(42, 200)
        assert await svc.due_wallet_threshold(42, 150) is None
        assert await svc.due_wallet_threshold(42, 100) == 100
        await svc.mark_wallet_notified(42, 100)
        assert await svc.due_wallet_threshold(42, 49) == 50
        await svc.mark_wallet_notified(42, 49)
        assert await svc.due_wallet_threshold(42, 0) is None
        assert await svc.due_wallet_threshold(42, 500) is None
        assert await svc.due_wallet_threshold(42, 190) == 200
        # Independent Telegram user ID, same balance: separate notification
        assert await svc.due_wallet_threshold(43, 190) == 200

    run(scenario())


def test_payg_reactivation_resets_status_notification_episode(tmp_path):
    svc = PaygNotificationService(str(tmp_path / "notices.db"))

    async def scenario():
        assert await svc.should_notify_status(12, "active") is False
        assert await svc.should_notify_status(12, "suspended") is True
        await svc.mark_status_notified(12, "suspended")
        assert await svc.should_notify_status(12, "suspended") is False
        assert await svc.should_notify_status(12, "active") is True
        await svc.mark_status_notified(12, "active")
        assert await svc.should_notify_status(12, "suspended") is True
        # Different PAYG panel of the same owner also has own transition.
        assert await svc.should_notify_status(13, "suspended") is True

    run(scenario())


def test_rebecca_status_alert_repeats_after_a_real_reactivation(tmp_path, monkeypatch):
    from group_ops_service import GroupOpsService
    service = GroupOpsService(str(tmp_path / "ops.db"))
    monkeypatch.setattr(rebecca_admin_ops, "group_ops_service", service)
    sent = AsyncMock(return_value=True)
    monkeypatch.setattr(rebecca_admin_ops, "_send_admin_alert", sent)
    admin = SimpleNamespace(id=8, user_id=42)

    async def scenario():
        await rebecca_admin_ops._maybe_status_alert(None, admin, "active")
        assert sent.await_count == 0
        await rebecca_admin_ops._maybe_status_alert(None, admin, "disabled")
        await rebecca_admin_ops._maybe_status_alert(None, admin, "disabled")
        await rebecca_admin_ops._maybe_status_alert(None, admin, "active")
        await rebecca_admin_ops._maybe_status_alert(None, admin, "disabled")
        assert sent.await_count == 3

    run(scenario())


def test_payg_retries_remote_suspension_when_first_attempt_fails(tmp_path, monkeypatch):
    path = str(tmp_path / "payg.db")
    svc = PaygService(path)
    admin = SimpleNamespace(
        id=77, user_id=42, marzban_username="payg42",
        is_active=True, deactivated_reason=None
    )
    fake_db = SimpleNamespace(
        get_admin_by_id=AsyncMock(return_value=admin),
        deactivate_admin=AsyncMock(return_value=True),
        reactivate_admin=AsyncMock(return_value=True),
    )
    monkeypatch.setattr(payg_module, "db", fake_db)
    remote = {"status": "active"}
    attempts = []

    async def find_admin(_username):
        return dict(remote)

    async def disable_admin(_username, _reason):
        attempts.append("disable")
        if len(attempts) == 1:
            raise RuntimeError("Rebecca outage")
        remote["status"] = "disabled"
        return {}

    monkeypatch.setattr(payg_module.rebecca_api, "get_admin_usage", AsyncMock(return_value=GIB))
    monkeypatch.setattr(payg_module.rebecca_api, "find_admin", find_admin)
    monkeypatch.setattr(payg_module.rebecca_api, "disable_admin", disable_admin)

    async def scenario():
        await svc.credit_wallet(42, 100, kind="seed")
        async with aiosqlite.connect(path) as conn:
            await conn.execute(
                """
                INSERT INTO payg_accounts (
                    user_id,admin_id,provider,status,price_per_gb_toman
                ) VALUES(42,77,'rebecca','active',10000)
                """
            )
            await conn.commit()
        with pytest.raises(RuntimeError, match="Rebecca outage"):
            await svc.sync_account(1)
        account = await svc.get_account(1)
        assert account["status"] == "suspended"
        # Second billing tick MUST retry even though old_status is suspended.
        result = await svc.sync_account(1)
        assert result.old_status == result.new_status == "suspended"
        assert remote["status"] == "disabled"
        assert attempts == ["disable", "disable"]
        fake_db.deactivate_admin.assert_awaited_once()

    run(scenario())


def test_payg_does_not_reactivate_manually_disabled_rebecca_admin(tmp_path, monkeypatch):
    path = str(tmp_path / "manual-hold.db")
    svc = PaygService(path)
    admin = SimpleNamespace(
        id=99, user_id=42, marzban_username="owner_hold",
        is_active=False, deactivated_reason="Disabled by owner"
    )
    fake_db = SimpleNamespace(
        get_admin_by_id=AsyncMock(return_value=admin),
        deactivate_admin=AsyncMock(return_value=True),
        reactivate_admin=AsyncMock(return_value=True),
    )
    monkeypatch.setattr(payg_module, "db", fake_db)
    monkeypatch.setattr(payg_module.rebecca_api, "get_admin_usage", AsyncMock(return_value=0))
    monkeypatch.setattr(payg_module.rebecca_api, "find_admin", AsyncMock(return_value={"status": "disabled"}))
    enable = AsyncMock()
    monkeypatch.setattr(payg_module.rebecca_api, "enable_admin", enable)

    async def scenario():
        await svc.credit_wallet(42, 1000, kind="seed")
        async with aiosqlite.connect(path) as conn:
            await conn.execute(
                "INSERT INTO payg_accounts(user_id,admin_id,provider,status,price_per_gb_toman) "
                "VALUES(42,99,'rebecca','suspended',10000)"
            )
            await conn.commit()
        with pytest.raises(payg_module.PaygError, match="manually disabled"):
            await svc.sync_account(1)
        enable.assert_not_awaited()
        fake_db.reactivate_admin.assert_not_awaited()

    run(scenario())


def test_payg_notification_failure_is_retryable(tmp_path):
    svc = PaygNotificationService(str(tmp_path / "retry.db"))

    async def scenario():
        assert await svc.due_wallet_threshold(42, 95) == 100
        # No mark -> transient Telegram failure, still pending.
        assert await svc.due_wallet_threshold(42, 95) == 100
        await svc.mark_wallet_notified(42, 95)
        assert await svc.due_wallet_threshold(42, 95) is None

        assert await svc.should_notify_status(71, "suspended") is True
        assert await svc.should_notify_status(71, "suspended") is True
        await svc.mark_status_notified(71, "suspended")
        assert await svc.should_notify_status(71, "suspended") is False

    run(scenario())
