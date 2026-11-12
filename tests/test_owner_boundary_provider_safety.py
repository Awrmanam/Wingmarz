"""Fail-closed owner/manager and provider boundaries on production-critical actions."""
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import config
import scheduler
from handlers import sudo_handlers as sudo
from utils.backup import BackupArtifact


class Callback:
    def __init__(self, data, user_id):
        self.data = data
        self.from_user = SimpleNamespace(id=user_id)
        self.message = SimpleNamespace(edit_text=AsyncMock(), answer=AsyncMock())
        self.answer = AsyncMock()
        self.bot = SimpleNamespace()


def state():
    return SimpleNamespace(clear=AsyncMock(), set_state=AsyncMock(), update_data=AsyncMock(), get_data=AsyncMock())


def run(coro):
    return asyncio.run(coro)


@pytest.mark.parametrize("handler_name,callback_data,with_state", [
    ("sudo_menu_backup", "sudo_menu_backup", False),
    ("backup_now", "backup_now", False),
    ("backup_restore_entry", "backup_restore", True),
    ("backup_schedule_entry", "backup_schedule", True),
])
def test_dynamic_manager_cannot_access_owner_backup_controls(monkeypatch, handler_name, callback_data, with_state):
    monkeypatch.setattr(config, "SUDO_ADMINS", [123, 456])
    monkeypatch.setattr(sudo, "is_owner", lambda uid: int(uid) == 123)
    cb, st = Callback(callback_data, 456), state()
    fn = getattr(sudo, handler_name)
    run(fn(cb, st) if with_state else fn(cb))
    cb.answer.assert_awaited_once()
    assert cb.answer.await_args.kwargs.get("show_alert") is True
    cb.message.edit_text.assert_not_awaited()
    st.set_state.assert_not_awaited()


@pytest.mark.parametrize("handler_name,has_text", [
    ("backup_restore_receive", False),
    ("backup_restore_password", True),
    ("backup_schedule_set", True),
])
def test_forged_fsm_cannot_restore_or_reconfigure_backup(monkeypatch, handler_name, has_text):
    monkeypatch.setattr(config, "SUDO_ADMINS", [123, 456])
    monkeypatch.setattr(sudo, "is_owner", lambda uid: int(uid) == 123)
    message = SimpleNamespace(from_user=SimpleNamespace(id=456), answer=AsyncMock())
    if has_text:
        message.text = "1h"
    else:
        message.document = SimpleNamespace(file_name="backup.zip")
    st = state()
    run(getattr(sudo, handler_name)(message, st))
    if handler_name == "backup_schedule_set":
        st.clear.assert_awaited_once()
        message.answer.assert_awaited_once()
    else:
        st.get_data.assert_not_awaited()
        message.answer.assert_not_awaited()


@pytest.mark.parametrize("handler_name,callback_data", [
    ("sudo_non_payer_entry", "sudo_non_payer"),
    ("sudo_non_payer_panel_selected", "sudo_non_payer_panel_7"),
    ("sudo_non_payer_confirm", "sudo_non_payer_confirm_7"),
])
def test_rebecca_cannot_run_marzban_nonpayment_actions(monkeypatch, handler_name, callback_data):
    monkeypatch.setattr(config, "PANEL_PROVIDER", "rebecca")
    monkeypatch.setattr(config, "SUDO_ADMINS", [123])
    monkeypatch.setattr(sudo, "is_owner", lambda uid: int(uid) == 123)
    db_list = AsyncMock(side_effect=AssertionError("should not touch database in Rebecca mode"))
    db_get = AsyncMock(side_effect=AssertionError("should not fetch panel"))
    m_api = AsyncMock(side_effect=AssertionError("Marzban called under Rebecca"))
    monkeypatch.setattr(sudo.db, "get_all_admins", db_list)
    monkeypatch.setattr(sudo.db, "get_admin_by_id", db_get)
    monkeypatch.setattr(sudo.marzban_api, "create_admin_api", m_api)
    cb = Callback(callback_data, 123)
    run(getattr(sudo, handler_name)(cb))
    cb.answer.assert_awaited_once()
    assert cb.answer.await_args.kwargs.get("show_alert") is True
    db_list.assert_not_awaited()
    db_get.assert_not_awaited()
    m_api.assert_not_awaited()


def test_hourly_archive_is_only_delivered_to_immutable_owners(tmp_path, monkeypatch):
    backup_file = tmp_path / "encrypted.zip"
    backup_file.write_bytes(b"fixture")
    artifact = BackupArtifact(
        path=backup_file,
        zip_password="not-printed",
        database_password=None,
        database_engine="SQLite",
        created_at=datetime.now(timezone.utc),
        size_bytes=backup_file.stat().st_size,
    )
    monkeypatch.setattr(config, "SUDO_ADMINS", [123, 456])
    monkeypatch.setattr(config, "BACKUP_RECIPIENTS", [123, 456])
    monkeypatch.setattr(scheduler, "OWNER_IDS", (123,))
    from utils import backup, backup_delivery
    monkeypatch.setattr(backup, "create_backup_zip", AsyncMock(return_value=artifact))
    delivery = AsyncMock()
    monkeypatch.setattr(backup_delivery, "send_backup_to_chat", delivery)
    bot = SimpleNamespace(send_message=AsyncMock())
    run(scheduler.MonitoringScheduler(bot).send_backup())
    assert [c.args[1] for c in delivery.await_args_list] == [123]
    bot.send_message.assert_not_awaited()
