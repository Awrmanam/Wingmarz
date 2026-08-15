from types import SimpleNamespace

import pytest
from aiogram import Bot

import config
import handlers.sudo_handlers as sudo
import handlers.admin_handlers as customer
from models.schemas import AdminModel
from rebecca_api import RebeccaAPIError
from utils.bold_fix_bot import BoldFixBot

def sync_test(function):
    from functools import wraps
    @wraps(function)
    def wrapper(*args, **kwargs):
        import asyncio
        return asyncio.run(function(*args, **kwargs))
    return wrapper



class Message:
    def __init__(self):
        self.edits = []
    async def edit_text(self, text, **kwargs):
        self.edits.append((text, kwargs))


class Callback:
    def __init__(self, data):
        self.data = data
        self.from_user = SimpleNamespace(id=99)
        self.message = Message()
        self.bot = SimpleNamespace()
        self.answers = []
    async def answer(self, text=None, **kwargs):
        self.answers.append((text, kwargs))


@pytest.fixture
def admin():
    return AdminModel(id=7, user_id=42, admin_name="Support",
                      marzban_username="armanstore2_support_8090",
                      marzban_password="must-not-be-used")


@pytest.fixture(autouse=True)
def rebecca_mode(monkeypatch, admin):
    monkeypatch.setattr(config, "PANEL_PROVIDER", "rebecca")
    monkeypatch.setattr(config, "SUDO_ADMINS", [99])
    async def get_admin(_): return admin
    monkeypatch.setattr(sudo.db, "get_admin_by_id", get_admin)
    async def forbidden(*args, **kwargs): raise AssertionError("Marzban called in Rebecca mode")
    monkeypatch.setattr(sudo.marzban_api, "update_admin_password", forbidden)
    monkeypatch.setattr(sudo.marzban_api, "delete_admin_completely", forbidden)
    monkeypatch.setattr(sudo.marzban_api, "create_admin_api", forbidden)


@pytest.mark.parametrize("remote_status, expected_calls", [("active", 1), ("disabled", 0)])
@sync_test
async def test_disable_orders_remote_then_local_and_is_idempotent(monkeypatch, remote_status, expected_calls):
    events = []
    async def find(_): return {"status": remote_status}
    async def disable(*args): events.append("remote")
    async def local(*args): events.append("local"); return True
    async def notify(*args): events.append("notify")
    monkeypatch.setattr(sudo.rebecca_api, "find_admin", find)
    monkeypatch.setattr(sudo.rebecca_api, "disable_admin", disable)
    monkeypatch.setattr(sudo.db, "deactivate_admin", local)
    monkeypatch.setattr(sudo, "notify_admin_deactivated", notify)
    callback = Callback("manage_action_deactivate_7")
    await sudo.manage_action_deactivate(callback)
    assert events.count("remote") == expected_calls
    assert events[-2:] == ["local", "notify"]
    assert callback.message.edits[-1][0].startswith("✅")


@sync_test
async def test_disable_remote_failure_keeps_local_and_does_not_notify(monkeypatch):
    called = []
    async def find(_): return {"status": "active"}
    async def fail(*args): raise RebeccaAPIError("offline")
    async def local(*args): called.append("local"); return True
    async def notify(*args): called.append("notify")
    monkeypatch.setattr(sudo.rebecca_api, "find_admin", find)
    monkeypatch.setattr(sudo.rebecca_api, "disable_admin", fail)
    monkeypatch.setattr(sudo.db, "deactivate_admin", local)
    monkeypatch.setattr(sudo, "notify_admin_deactivated", notify)
    callback = Callback("manage_action_deactivate_7")
    await sudo.manage_action_deactivate(callback)
    assert called == []
    assert not callback.message.edits[-1][0].startswith("✅")


@pytest.mark.parametrize("remote_status, expected_calls", [("disabled", 1), ("active", 0)])
@sync_test
async def test_enable_orders_remote_then_local_and_is_idempotent(monkeypatch, remote_status, expected_calls):
    events = []
    async def find(_): return {"status": remote_status}
    async def enable(*args): events.append("remote")
    async def local(*args): events.append("local"); return True
    async def notify(*args): events.append("notify")
    monkeypatch.setattr(sudo.rebecca_api, "find_admin", find)
    monkeypatch.setattr(sudo.rebecca_api, "enable_admin", enable)
    monkeypatch.setattr(sudo.db, "reactivate_admin", local)
    monkeypatch.setattr(sudo, "notify_admin_reactivation_utils", notify)
    callback = Callback("manage_action_activate_7")
    await sudo.manage_action_activate(callback)
    assert events.count("remote") == expected_calls
    assert events[-2:] == ["local", "notify"]


@pytest.mark.parametrize("action", ["disable", "enable"])
@sync_test
async def test_local_sync_failure_is_not_success_and_not_notified(monkeypatch, action):
    notifications = []
    async def find(_): return {"status": "active" if action == "disable" else "disabled"}
    async def remote(*args): pass
    async def local(*args): return False
    async def notify(*args): notifications.append(1)
    monkeypatch.setattr(sudo.rebecca_api, "find_admin", find)
    monkeypatch.setattr(sudo.rebecca_api, f"{action}_admin", remote)
    monkeypatch.setattr(sudo.db, "deactivate_admin" if action == "disable" else "reactivate_admin", local)
    monkeypatch.setattr(sudo, "notify_admin_deactivated" if action == "disable" else "notify_admin_reactivation_utils", notify)
    callback = Callback(f"manage_action_{'deactivate' if action == 'disable' else 'activate'}_7")
    await (sudo.manage_action_deactivate(callback) if action == "disable" else sudo.manage_action_activate(callback))
    assert notifications == []
    assert "همگام‌سازی" in callback.message.edits[-1][0]
    assert not callback.message.edits[-1][0].startswith("✅")


@sync_test
async def test_delete_first_click_only_confirms(monkeypatch):
    async def forbidden(*args): raise AssertionError("delete called on first click")
    monkeypatch.setattr(sudo, "delete_admin_panel_completely", forbidden)
    callback = Callback("manage_action_delete_7")
    await sudo.manage_action_delete(callback)
    assert "حذف کامل" in callback.message.edits[-1][0]
    assert callback.message.edits[-1][1]["reply_markup"].inline_keyboard[0][0].callback_data == "manage_confirm_delete_7"


@pytest.mark.parametrize("remote_ok", [True, False])
@sync_test
async def test_delete_confirmation_remote_controls_local_removal(monkeypatch, admin, remote_ok):
    removed = []
    async def delete(_):
        if not remote_ok: raise RebeccaAPIError("offline")
        return True
    async def remove(_): removed.append(1); return True
    async def log(_): return True
    monkeypatch.setattr(sudo.rebecca_api, "delete_admin", delete)
    monkeypatch.setattr(sudo.db, "remove_admin_by_id", remove)
    monkeypatch.setattr(sudo.db, "add_log", log)
    callback = Callback("manage_confirm_delete_7")
    await sudo.manage_confirm_delete(callback)
    assert removed == ([1] if remote_ok else [])
    assert callback.message.edits[-1][0].startswith("✅" if remote_ok else "❌")


@sync_test
async def test_delete_reports_remote_local_inconsistency(monkeypatch):
    async def delete(_): return True
    async def remove(_): return False
    async def log(_): return True
    monkeypatch.setattr(sudo.rebecca_api, "delete_admin", delete)
    monkeypatch.setattr(sudo.db, "remove_admin_by_id", remove)
    monkeypatch.setattr(sudo.db, "add_log", log)
    callback = Callback("manage_confirm_delete_7")
    await sudo.manage_confirm_delete(callback)
    text = callback.message.edits[-1][0]
    assert "از Rebecca حذف شد" in text
    assert "همگام‌سازی دستی" in text
    assert not text.startswith("✅")


@sync_test
async def test_rebecca_counts_render_remote_values(monkeypatch):
    async def find(_): return {"users_count": 1, "active_users": 1, "online_users": 0,
                               "disabled_users": 0, "expired_users": 0}
    monkeypatch.setattr(sudo.rebecca_api, "find_admin", find)
    callback = Callback("manage_action_users_7")
    await sudo.manage_action_users(callback)
    text = callback.message.edits[-1][0]
    assert "👥 تعداد کاربران: 1" in text
    assert "🟢 فعال: 1" in text


@sync_test
async def test_customer_info_uses_raw_usage_and_explicit_html(monkeypatch, admin):
    async def find(_): return {"status": "active", "users_count": 1, "active_users": 1,
                               "online_users": 0, "disabled_users": 0, "expired_users": 0,
                               "data_limit": 2048}
    async def usage(_): return 1024
    monkeypatch.setattr(customer.rebecca_api, "find_admin", find)
    monkeypatch.setattr(customer.rebecca_api, "get_admin_usage", usage)
    callback = Callback("my_info")
    await customer.show_admin_info(callback, admin)
    text, kwargs = callback.answers[-1]
    assert "<code>armanstore2_support_8090</code>" in text
    assert "1.00 KB" in text and "2.00 KB" in text
    assert kwargs["parse_mode"] == "HTML"


@sync_test
async def test_sudo_info_username_survives_actual_boldfix_layer(monkeypatch):
    async def find(_): return {"status": "active", "users_count": 1, "active_users": 1,
                               "online_users": 0, "disabled_users": 0, "expired_users": 0}
    async def usage(_): return 1024
    monkeypatch.setattr(sudo.rebecca_api, "find_admin", find)
    monkeypatch.setattr(sudo.rebecca_api, "get_admin_usage", usage)
    callback = Callback("manage_action_info_7")
    await sudo.manage_action_info(callback)
    text, kwargs = callback.message.edits[-1]
    assert kwargs["parse_mode"] == "HTML"
    assert "<code>armanstore2_support_8090</code>" in text

    captured = {}
    async def capture(self, method, *args, **kwargs):
        captured["method"] = method
        return SimpleNamespace()
    monkeypatch.setattr(Bot, "__call__", capture)
    bot = BoldFixBot("123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi")
    try:
        await bot.send_message(42, text, parse_mode="HTML")
    finally:
        await bot.session.close()
    assert captured["method"].text == text
    assert "armanstore2_support_8090" in captured["method"].text
