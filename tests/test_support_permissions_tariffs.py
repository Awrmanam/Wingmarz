import asyncio
import time
from pathlib import Path

import aiosqlite

import config
import support_service
from message_catalog import UI_MESSAGES, UI_TITLES
from rebecca_permissions import permissions_are_user_only, standard_user_only_permissions
from ui_presentation_registry import resolve_button


def run(coro):
    return asyncio.run(coro)


def test_rebecca_managed_admin_profile_is_standard_user_only():
    permissions = standard_user_only_permissions()
    assert permissions_are_user_only(permissions)
    assert permissions["sections"] == {
        "usage": False,
        "admins": False,
        "services": False,
        "hosts": False,
        "nodes": False,
        "integrations": False,
        "xray": False,
    }
    assert all(value is False for value in permissions["admin_management"].values())
    assert all(value is False for value in permissions["sudo"].values())
    for key, value in permissions["users"].items():
        if key != "max_data_limit_per_user":
            assert value is True


def test_rebecca_create_and_startup_repair_use_explicit_user_only_permissions():
    api_source = Path("rebecca_api.py").read_text(encoding="utf-8")
    sync_source = Path("rebecca_permissions_sync.py").read_text(encoding="utf-8")
    bootstrap_source = Path("handlers/operations_bootstrap.py").read_text(encoding="utf-8")
    assert '"role": "standard"' in api_source
    assert '"permissions": standard_user_only_permissions()' in api_source
    assert "enforce_standard_user_only" in sync_source
    assert "reconcile_managed_admin_permissions" in bootstrap_source


def test_ticket_close_is_owner_bound_atomic_and_irreversible(tmp_path, monkeypatch):
    db_path = str(tmp_path / "support.db")
    monkeypatch.setattr(config, "DATABASE_PATH", db_path)

    async def scenario():
        ticket_id = await support_service.create_ticket(101, "مشکل اتصال", "لطفاً بررسی کنید")
        assert await support_service.close_ticket(ticket_id, 202, expected_user_id=999) is None
        still_open = await support_service.get_ticket(ticket_id, user_id=101)
        assert still_open["status"] == "open"

        closed = await support_service.close_ticket(ticket_id, 101, expected_user_id=101)
        assert closed is not None
        assert closed["status"] == "closed"
        after = await support_service.get_ticket(ticket_id, user_id=101)
        assert after["status"] == "closed"
        assert after["closed_by"] == 101
        assert after["closed_at"] is not None

        # There is deliberately no reopen path; a second close is also a no-op.
        assert await support_service.close_ticket(ticket_id, 101, expected_user_id=101) is None

    run(scenario())


def test_my_tickets_are_split_into_open_and_closed(tmp_path, monkeypatch):
    db_path = str(tmp_path / "support.db")
    monkeypatch.setattr(config, "DATABASE_PATH", db_path)

    async def scenario():
        open_id = await support_service.create_ticket(7, "باز", "متن")
        closed_id = await support_service.create_ticket(7, "بسته", "متن")
        await support_service.create_ticket(8, "کاربر دیگر", "متن")
        assert await support_service.close_ticket(closed_id, 7, expected_user_id=7)

        open_items, open_total, *_ = await support_service.list_user_tickets(7, status="open")
        closed_items, closed_total, *_ = await support_service.list_user_tickets(7, status="closed")
        assert open_total == 1 and [item["id"] for item in open_items] == [open_id]
        assert closed_total == 1 and [item["id"] for item in closed_items] == [closed_id]

    run(scenario())


def test_ticket_ui_has_confirmations_notifications_and_view_action():
    source = Path("handlers/support_v2.py").read_text(encoding="utf-8")
    assert "آیا مطمئن هستید؟" in source
    assert "از بستن این تیکت مطمئن هستید؟" in source
    assert "این تیکت دوباره باز نمی‌شود" in source
    assert "تیکت‌های من" in source
    assert "تیکت‌های باز" in source
    assert "تیکت‌های بسته" in source
    assert "مشاهده تیکت" in source
    assert "تیکت توسط پشتیبانی بسته شده" in source


def test_tariffs_are_on_canonical_home_and_copy_is_runtime_editable():
    home_source = Path("handlers/home_keyboards.py").read_text(encoding="utf-8")
    tariffs_source = Path("handlers/tariffs.py").read_text(encoding="utf-8")
    assert '"tariffs:home"' in home_source
    assert "تعرفه‌ها" in home_source
    assert "tariffs_page" in UI_MESSAGES
    assert UI_TITLES["tariffs_page"] == "صفحه تعرفه‌ها"
    resolved = resolve_button("tariffs:home", "تعرفه‌ها")
    assert resolved.button is not None
    # Tariffs is deliberately content-only: one editable message plus one Back button.
    assert '"tariffs_page"' in tariffs_source
    assert "_buy_callback" not in tariffs_source
    assert '"svcmarket:trial"' not in tariffs_source
    assert tariffs_source.count("await _button(") == 1
    assert '"⬅️ بازگشت"' in tariffs_source


def test_active_panel_trial_restore_precedes_cooldown_and_issuance_router():
    composition = Path("handlers/__init__.py").read_text(encoding="utf-8")
    restore = Path("handlers/panel_trial_restore.py").read_text(encoding="utf-8")
    assert composition.index("include_router(panel_trial_restore_router)") < composition.index("include_router(trial_ui_v2_router)")
    assert "load_active_panel_trial" in restore
    assert "await trial.panel_trial_selected(callback, state)" in restore
    assert "پنل تست فعال شما" in restore
