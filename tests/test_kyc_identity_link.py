import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from kyc_service import KycService
import rebecca_identity_link as identity
from kyc_middleware import _target_admin_id
import handlers.kyc as kyc_handlers


def run(coro):
    return asyncio.run(coro)


def test_kyc_default_is_required_and_can_be_exempted(tmp_path):
    service = KycService(str(tmp_path / "kyc.db"))

    async def scenario():
        assert await service.is_required(77) is True
        await service.set_required(77, False)
        assert await service.is_required(77) is False
        await service.set_required(77, True)
        assert await service.is_required(77) is True

    run(scenario())


def test_kyc_submission_review_and_resubmission(tmp_path):
    service = KycService(str(tmp_path / "kyc.db"))

    async def scenario():
        await service.submit(123, "Ali Rezaei", "telegram-file-1", "uniq-1")
        profile = await service.get_profile(123)
        assert profile["status"] == "pending"
        assert profile["full_name"] == "Ali Rezaei"
        assert await service.is_approved(123) is False

        assert await service.approve(123, 999) is True
        assert await service.is_approved(123) is True
        # Idempotent review: an already reviewed request is not changed twice.
        assert await service.reject(123, 999) is False

        # A user can explicitly resubmit after an approval/rejection.
        await service.submit(123, "Ali Rezaei", "telegram-file-2", "uniq-2")
        profile = await service.get_profile(123)
        assert profile["status"] == "pending"
        assert profile["document_file_id"] == "telegram-file-2"
        assert await service.reject(123, 999) is True
        assert (await service.get_profile(123))["status"] == "rejected"

    run(scenario())


def test_callback_admin_id_parser_covers_sensitive_panel_actions():
    assert _target_admin_id("info_panel_12") == 12
    assert _target_admin_id("report_panel_13") == 13
    assert _target_admin_id("users_panel_14") == 14
    assert _target_admin_id("admin_renew_panel_15") == 15
    assert _target_admin_id("admin_renew_traffic_amount_16_50") == 16
    assert _target_admin_id("admin_full_renew_plan_17_3") == 17
    assert _target_admin_id("admin_buy_reseller") is None


def test_rebecca_start_auto_links_only_matching_standard_admins(monkeypatch):
    remotes = [
        {
            "username": "seller-a",
            "role": "standard",
            "telegram_id": 555,
            "status": "active",
            "users_limit": 20,
            "data_limit": None,
        },
        {
            "username": "seller-b",
            "role": "standard",
            "telegram_id": 777,
            "status": "active",
        },
        {
            "username": "owner",
            "role": "full_access",
            "telegram_id": 555,
            "status": "active",
        },
    ]
    monkeypatch.setattr(identity.config, "PANEL_PROVIDER", "rebecca")
    monkeypatch.setattr(identity.rebecca_api, "list_admins", AsyncMock(return_value=remotes))
    sync_one = AsyncMock(return_value=44)
    monkeypatch.setattr(identity, "_sync_one", sync_one)

    linked = run(identity.sync_rebecca_admins_for_telegram(
        555,
        telegram_username="telegram_user",
        first_name="Ali",
        last_name="R",
    ))

    assert linked == [44]
    sync_one.assert_awaited_once()
    remote = sync_one.call_args.args[0]
    assert remote["username"] == "seller-a"


def test_sync_one_rebinds_existing_local_panel_to_authoritative_telegram_id(monkeypatch):
    existing = SimpleNamespace(
        id=8,
        user_id=111,
        login_url=None,
    )
    monkeypatch.setattr(identity.db, "get_admin_by_marzban_username", AsyncMock(return_value=existing))
    update = AsyncMock(return_value=True)
    monkeypatch.setattr(identity.db, "update_admin", update)

    remote = {
        "username": "seller-a",
        "role": "standard",
        "telegram_id": "555",
        "status": "active",
        "users_limit": 10,
        "data_limit": 20 * 1024**3,
        "expire": None,
    }

    admin_id = run(identity._sync_one(remote, telegram_username="tgname"))

    assert admin_id == 8
    kwargs = update.call_args.kwargs
    assert kwargs["user_id"] == 555
    assert kwargs["is_active"] == 1
    assert kwargs["max_users"] == 10
    assert kwargs["max_total_traffic"] == 20 * 1024**3
    assert kwargs["username"] == "tgname"


def test_sudo_start_does_not_consume_regular_user_start():
    source = open("handlers/sudo_handlers.py", encoding="utf-8").read()
    assert '@sudo_router.message(Command("start"), F.from_user.id.in_(config.SUDO_ADMINS))' in source



def test_buyer_can_start_kyc_before_any_panel_exists(monkeypatch):
    callback = SimpleNamespace(
        from_user=SimpleNamespace(id=321),
        message=SimpleNamespace(edit_text=AsyncMock()),
        answer=AsyncMock(),
    )
    state = SimpleNamespace(clear=AsyncMock(), set_state=AsyncMock())

    run(kyc_handlers.kyc_start(callback, state))

    state.clear.assert_awaited_once()
    state.set_state.assert_awaited_once_with(kyc_handlers.KycStates.waiting_full_name)
    callback.message.edit_text.assert_awaited_once()
    assert "احراز هویت" in callback.message.edit_text.await_args.args[0]
