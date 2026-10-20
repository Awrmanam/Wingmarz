import asyncio
from unittest.mock import AsyncMock

import pytest

import trust_service as trust_module
from group_ops_service import GroupOpsService
from handlers.group_ops import (
    OPS_CHAT_REQUEST_ID,
    _domain_value,
    _merge_subscription_settings,
    _ops_group_picker_keyboard,
    _ops_home_keyboard,
    _support_value,
)
from rebecca_admin_ops import _percent_rank, _time_rank
from rebecca_api import RebeccaAPI
import rebecca_renewal as renewal
from types import SimpleNamespace
from scheduler import MonitoringScheduler
import config


def run(coro):
    return asyncio.run(coro)


class _FakeKyc:
    def __init__(self, approved=False):
        self.approved = approved

    async def is_approved(self, user_id):
        return self.approved


def test_manual_trust_and_auto_approve_require_real_payment_match(tmp_path, monkeypatch):
    service = trust_module.TrustService(str(tmp_path / "trust.db"))
    monkeypatch.setattr(trust_module, "kyc_service", _FakeKyc(False))

    async def scenario():
        assert await service.is_identity_approved(55) is False
        with pytest.raises(ValueError):
            await service.set_auto_approve(55, True)

        await service.set_manual_trusted(55, True, 999)
        assert await service.is_identity_approved(55) is True

        await service.set_auto_approve(55, True)
        assert await service.auto_approve_enabled(55) is True

        # Receipt/trust alone is never sufficient.
        assert await service.can_auto_approve(55, 91) is False

        await service.set_payment_verification(
            91,
            "matched",
            provider="bank-test",
            provider_transaction_id="TX-UNIQUE-91",
            matched_amount_toman=100_000,
        )
        assert await service.can_auto_approve(55, 91) is True
        assert await service.can_auto_approve(55, 92) is False

        # Revoking manual trust conservatively disables auto-approve too.
        await service.set_manual_trusted(55, False, 999)
        assert await service.is_identity_approved(55) is False
        assert await service.auto_approve_enabled(55) is False

    run(scenario())


def test_kyc_approval_can_enable_auto_approve_without_manual_trust(tmp_path, monkeypatch):
    service = trust_module.TrustService(str(tmp_path / "trust.db"))
    monkeypatch.setattr(trust_module, "kyc_service", _FakeKyc(True))

    async def scenario():
        assert await service.is_identity_approved(77) is True
        await service.set_auto_approve(77, True)
        assert await service.auto_approve_enabled(77) is True

    run(scenario())


def test_bank_transaction_id_cannot_be_claimed_by_two_orders(tmp_path):
    service = trust_module.TrustService(str(tmp_path / "trust.db"))

    async def scenario():
        await service.set_payment_verification(
            1, "matched", provider="bank", provider_transaction_id="same-tx"
        )
        with pytest.raises(Exception):
            await service.set_payment_verification(
                2, "matched", provider="bank", provider_transaction_id="same-tx"
            )

    run(scenario())


def test_group_event_claim_is_idempotent(tmp_path):
    service = GroupOpsService(str(tmp_path / "ops.db"))

    async def scenario():
        assert await service.claim_event("order:10:created") is True
        assert await service.claim_event("order:10:created") is False
        assert await service.claim_event("order:11:created") is True

    run(scenario())


def test_subscription_helpers_preserve_other_admin_overrides():
    current = {
        "subscription_profile_title": "Reseller A",
        "custom_flag": "keep-me",
    }
    merged = _merge_subscription_settings(
        current, "subscription_support_url", "https://t.me/support_a"
    )
    assert merged["subscription_profile_title"] == "Reseller A"
    assert merged["custom_flag"] == "keep-me"
    assert merged["subscription_support_url"] == "https://t.me/support_a"
    # Function must not mutate the source map.
    assert "subscription_support_url" not in current

    cleared = _merge_subscription_settings(merged, "subscription_support_url", "")
    assert "subscription_support_url" not in cleared
    assert cleared["custom_flag"] == "keep-me"


def test_group_ops_subscription_input_validation():
    assert _domain_value("Sub.Example.COM.") == "sub.example.com"
    assert _domain_value("-") is None
    with pytest.raises(ValueError):
        _domain_value("https://sub.example.com/path")

    assert _support_value("@arman_support") == "https://t.me/arman_support"
    assert _support_value("https://example.com/support") == "https://example.com/support"
    assert _support_value("-") == ""
    with pytest.raises(ValueError):
        _support_value("123456789")


def test_admin_alert_threshold_ranks():
    assert _percent_rank(69.9) == 0
    assert _percent_rank(70) == 1
    assert _percent_rank(94.9) == 3
    assert _percent_rank(100) == 5

    assert _time_rank(8 * 86400) == 0
    assert _time_rank(7 * 86400) == 1
    assert _time_rank(2 * 86400) == 2
    assert _time_rank(5 * 3600) == 4
    assert _time_rank(-1) == 5


def test_rebecca_admin_management_api_paths(monkeypatch):
    api = RebeccaAPI()
    request = AsyncMock()
    monkeypatch.setattr(api, "_request", request)

    async def scenario():
        request.return_value = {"username": "seller", "data_limit": 123}
        updated = await api.update_admin_fields("seller", {"data_limit": 123})
        assert updated["username"] == "seller"
        request.assert_awaited_with(
            "PUT", "/api/admin/seller", json={"data_limit": 123}
        )

        request.reset_mock()
        request.return_value = {"id": 7, "username": "seller", "subscription_domain": "sub.example.com"}
        result = await api.update_admin_subscription_settings(
            7, {"subscription_domain": "sub.example.com"}
        )
        assert result["id"] == 7
        request.assert_awaited_with(
            "PUT",
            "/api/settings/subscriptions/admins/7",
            json={"subscription_domain": "sub.example.com"},
        )

    run(scenario())


def test_rebecca_scheduler_has_group_ops_jobs_but_no_duplicate_payg(monkeypatch):
    monkeypatch.setattr(config, "PANEL_PROVIDER", "rebecca")
    monkeypatch.setattr(config, "MONITORING_INTERVAL", 60)
    scheduler = MonitoringScheduler(bot=None)

    async def scenario():
        await scheduler.start()
        try:
            assert scheduler.scheduler.get_job("group_ops_admin_alerts") is not None
            assert scheduler.scheduler.get_job("group_ops_daily_summary") is not None
            assert scheduler.scheduler.get_job("payg_billing_job") is None
        finally:
            await scheduler.stop()

    run(scenario())


def test_incremental_rebecca_renewal_updates_remote_and_local(monkeypatch):
    admin = SimpleNamespace(
        id=7,
        user_id=555,
        marzban_username="seller",
        max_total_time=30 * 86400,
        validity_days=30,
    )
    order = {
        "id": 91,
        "status": "submitted",
        "target_admin_id": 7,
        "apply_as_reset": 0,
        "delta_traffic_bytes": 10 * 1024**3,
        "delta_time_seconds": 30 * 86400,
        "delta_users": 5,
    }
    monkeypatch.setattr(renewal.db, "get_admin_by_id", AsyncMock(return_value=admin))
    monkeypatch.setattr(renewal.db, "update_admin", AsyncMock(return_value=True))
    monkeypatch.setattr(renewal.db, "update_order", AsyncMock(return_value=True))
    monkeypatch.setattr(renewal.rebecca_api, "find_admin", AsyncMock(return_value={
        "username": "seller",
        "data_limit": 100 * 1024**3,
        "expire": 4_000_000_000,
        "users_limit": 20,
    }))
    update_remote = AsyncMock(return_value={"username": "seller"})
    monkeypatch.setattr(renewal.rebecca_api, "update_admin_fields", update_remote)
    monkeypatch.setattr(renewal.group_ops_service, "audit", AsyncMock())

    result = run(renewal.apply_rebecca_renewal(order, 999))

    assert result.admin_id == 7
    assert result.user_id == 555
    payload = update_remote.await_args.args[1]
    assert payload["data_limit"] == 110 * 1024**3
    assert payload["expire"] == 4_000_000_000 + 30 * 86400
    assert payload["users_limit"] == 25
    renewal.db.update_order.assert_awaited_once_with(91, status="approved", approved_by=999)


def test_full_rebecca_renewal_resets_usage(monkeypatch):
    admin = SimpleNamespace(
        id=8,
        user_id=777,
        marzban_username="seller2",
        max_total_time=10,
        validity_days=1,
    )
    plan = SimpleNamespace(
        id=3,
        name="Pro 90",
        traffic_limit_bytes=300 * 1024**3,
        time_limit_seconds=90 * 86400,
        max_users=50,
    )
    order = {
        "id": 92,
        "status": "submitted",
        "target_admin_id": 8,
        "apply_as_reset": 1,
        "plan_id": 3,
    }
    monkeypatch.setattr(renewal.db, "get_admin_by_id", AsyncMock(return_value=admin))
    monkeypatch.setattr(renewal.db, "get_plan_by_id", AsyncMock(return_value=plan))
    monkeypatch.setattr(renewal.db, "update_admin", AsyncMock(return_value=True))
    monkeypatch.setattr(renewal.db, "update_order", AsyncMock(return_value=True))
    monkeypatch.setattr(renewal.rebecca_api, "find_admin", AsyncMock(return_value={
        "username": "seller2", "data_limit": 1, "expire": 1, "users_limit": 1
    }))
    monkeypatch.setattr(renewal.rebecca_api, "update_admin_fields", AsyncMock(return_value={"username": "seller2"}))
    reset = AsyncMock(return_value={"username": "seller2"})
    monkeypatch.setattr(renewal.rebecca_api, "reset_admin_usage", reset)
    monkeypatch.setattr(renewal.group_ops_service, "audit", AsyncMock())

    result = run(renewal.apply_rebecca_renewal(order, 999))

    assert result.admin_id == 8
    reset.assert_awaited_once_with("seller2")
    renewal.db.update_order.assert_awaited_once_with(92, status="approved", approved_by=999)



def test_group_ops_button_first_keyboards_are_valid():
    home = _ops_home_keyboard()
    callbacks = [
        button.callback_data
        for row in home.inline_keyboard
        for button in row
        if button.callback_data
    ]
    assert callbacks == [
        "gops:connect",
        "gops:repair",
        "gops:status",
        "back_to_main",
    ]
    assert "gops:admins:0" not in callbacks
    assert "gops:search" not in callbacks

    picker = _ops_group_picker_keyboard()
    button = picker.keyboard[0][0]
    assert button.request_chat is not None
    assert button.request_chat.request_id == OPS_CHAT_REQUEST_ID
    assert button.request_chat.chat_is_channel is False
    assert button.request_chat.chat_is_forum is True
    assert button.request_chat.bot_is_member is True


def test_group_ops_can_forget_stale_topic_ids(tmp_path):
    service = GroupOpsService(str(tmp_path / "ops.db"))

    async def scenario():
        await service.ensure_schema()
        async with __import__("aiosqlite").connect(service.db_path) as conn:
            await conn.execute(
                "INSERT INTO group_ops_topics(topic_key,title,thread_id) VALUES(?,?,?)",
                ("admins", "Admins", 123),
            )
            await conn.commit()
        assert "admins" in await service.topics()
        await service.clear_topics()
        assert await service.topics() == {}

    run(scenario())


def test_legacy_sudo_keyboard_only_bridges_to_canonical_dashboard():
    from handlers.sudo_handlers import get_sudo_keyboard

    kb = get_sudo_keyboard()
    callbacks = [
        button.callback_data
        for row in kb.inline_keyboard
        for button in row
        if button.callback_data
    ]
    assert callbacks == ["back_to_main"]



def test_canonical_operational_dashboard_is_clean_and_nonduplicated(monkeypatch):
    from aiogram.types import InlineKeyboardButton
    from handlers import operations

    assert len(operations.MENU_SPECS) == 6
    assert {spec[0] for spec in operations.MENU_SPECS} == {
        "ops:hub:panels",
        "ops:hub:sales",
        "ops:hub:finance",
        "ops:hub:customers",
        "ops:hub:monitor",
        "ops:hub:settings",
    }

    visible_callbacks = [
        item[0]
        for hub in operations.HUB_MENUS.values()
        for item in hub["items"]
    ]
    assert len(visible_callbacks) == len(set(visible_callbacks))
    for legacy in {
        "sudo_menu_sales",
        "sudo_menu_panels",
        "sudo_menu_reports",
        "sudo_menu_settings",
    }:
        assert legacy not in visible_callbacks
        assert legacy not in {spec[0] for spec in operations.MENU_SPECS}

    assert "gops:home" in visible_callbacks
    assert "paygsvcadmin:home" in visible_callbacks
    assert "rebecca_services" in visible_callbacks

    async def visible(_callback):
        return True

    async def label(_callback, default):
        return default

    async def button(text, callback_data, **kwargs):
        return InlineKeyboardButton(text=text, callback_data=callback_data)

    monkeypatch.setattr(operations.operations_service, "menu_is_visible", visible)
    monkeypatch.setattr(operations, "_menu_label", label)
    monkeypatch.setattr(operations, "_button", button)

    keyboard = run(operations.build_operational_dashboard_keyboard())
    callbacks = [
        item.callback_data
        for row in keyboard.inline_keyboard
        for item in row
        if item.callback_data
    ]
    assert callbacks == [
        "gops:admins:0",
        "cc:orders:0",
        "ops:hub:panels",
        "ops:hub:sales",
        "ops:hub:finance",
        "ops:hub:customers",
        "ops:hub:monitor",
        "ops:hub:settings",
    ]
