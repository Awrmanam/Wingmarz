import asyncio
from unittest.mock import AsyncMock

import pytest

import trust_service as trust_module
from group_ops_service import GroupOpsService
from handlers.group_ops import _domain_value, _merge_subscription_settings, _support_value
from rebecca_admin_ops import _percent_rank, _time_rank
from rebecca_api import RebeccaAPI
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
