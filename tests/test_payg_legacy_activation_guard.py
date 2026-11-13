"""Old Telegram keyboards cannot reactivate a financially held Rebecca admin."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiosqlite

import config
from handlers import sudo_handlers as sudo
from payg_service import PaygService


def _callback():
    return SimpleNamespace(
        data="manage_action_activate_77",
        from_user=SimpleNamespace(id=9001),
        message=SimpleNamespace(edit_text=AsyncMock()),
        answer=AsyncMock(),
    )


def test_replayed_legacy_activate_button_respects_financial_hold(monkeypatch):
    monkeypatch.setattr(config,"SUDO_ADMINS",[9001])
    monkeypatch.setattr(config,"PANEL_PROVIDER","rebecca")
    monkeypatch.setattr(
        sudo.db,"get_admin_by_id",
        AsyncMock(return_value=SimpleNamespace(id=77,marzban_username="owner")),
    )
    monkeypatch.setattr(
        sudo.payg_service,"get_admin_account",
        AsyncMock(return_value={"id":12,"admin_id":77,"status":"suspended"}),
    )
    remote=AsyncMock()
    monkeypatch.setattr(sudo.rebecca_api,"enable_admin",remote)
    callback=_callback()
    asyncio.run(sudo.manage_action_activate(callback))
    remote.assert_not_awaited()
    callback.message.edit_text.assert_awaited_once()
    text=callback.message.edit_text.await_args.args[0]
    assert "PAYG" in text
    buttons=callback.message.edit_text.await_args.kwargs["reply_markup"]
    assert any(b.callback_data=="paygaudit:holds"
               for row in buttons.inline_keyboard for b in row)


def test_payg_lookup_failure_fails_closed(monkeypatch):
    monkeypatch.setattr(config,"SUDO_ADMINS",[9001])
    monkeypatch.setattr(config,"PANEL_PROVIDER","rebecca")
    monkeypatch.setattr(
        sudo.db,"get_admin_by_id",
        AsyncMock(return_value=SimpleNamespace(id=77,marzban_username="owner")),
    )
    monkeypatch.setattr(
        sudo.payg_service,"get_admin_account",
        AsyncMock(side_effect=OSError("isolated DB offline")),
    )
    remote=AsyncMock()
    monkeypatch.setattr(sudo.rebecca_api,"enable_admin",remote)
    callback=_callback()
    asyncio.run(sudo.manage_action_activate(callback))
    remote.assert_not_awaited()
    callback.answer.assert_awaited_once()
    assert callback.answer.await_args.kwargs["show_alert"]


def test_admin_scoped_query_does_not_use_another_resellers_account(tmp_path):
    svc=PaygService(str(tmp_path/"finance.sqlite"))

    async def scenario():
        await svc.ensure_schema()
        async with aiosqlite.connect(svc.db_path) as conn:
            await conn.executemany(
                "INSERT INTO payg_accounts("
                "user_id,admin_id,provider,status,price_per_gb_toman)"
                " VALUES(8,?,'rebecca',?,1000)",
                [(77,"suspended"),(78,"active")],
            )
            await conn.commit()
        assert (await svc.get_admin_account(77))["status"]=="suspended"
        assert (await svc.get_admin_account(78))["status"]=="active"
        assert await svc.get_admin_account(999) is None

    asyncio.run(scenario())


def test_old_activate_panel_keyboard_cannot_bypass_payg_hold(monkeypatch):
    """A stale 'activate_panel_<id>' button uses the protected path."""
    monkeypatch.setattr(config, "SUDO_ADMINS", [9001])
    monkeypatch.setattr(config, "PANEL_PROVIDER", "rebecca")
    monkeypatch.setattr(
        sudo.db, "get_admin_by_id",
        AsyncMock(return_value=SimpleNamespace(
            id=77, user_id=321, is_active=False,
            marzban_username="owner", deactivated_reason="PAYG balance exhausted",
        )),
    )
    monkeypatch.setattr(
        sudo.payg_service, "get_admin_account",
        AsyncMock(return_value={"id":12,"admin_id":77,"status":"suspended"}),
    )
    legacy_credential_restore=AsyncMock()
    monkeypatch.setattr(sudo, "restore_admin_password_and_update_db", legacy_credential_restore)
    local_activation=AsyncMock()
    monkeypatch.setattr(sudo.db, "reactivate_admin", local_activation)
    remote_activation=AsyncMock()
    monkeypatch.setattr(sudo.rebecca_api, "enable_admin", remote_activation)
    cb=_callback()
    cb.data="activate_panel_77"
    asyncio.run(sudo.activate_panel_selected(cb))
    local_activation.assert_not_awaited()
    remote_activation.assert_not_awaited()
    legacy_credential_restore.assert_not_awaited()
    cb.message.edit_text.assert_awaited_once()
    assert "PAYG" in cb.message.edit_text.await_args.args[0]


def test_old_activate_panel_keyboard_fails_closed_on_finance_error(monkeypatch):
    monkeypatch.setattr(config, "SUDO_ADMINS", [9001])
    monkeypatch.setattr(config, "PANEL_PROVIDER", "rebecca")
    monkeypatch.setattr(
        sudo.db,"get_admin_by_id",AsyncMock(return_value=SimpleNamespace(
            id=77,user_id=321,is_active=False,marzban_username="owner",
        )),
    )
    monkeypatch.setattr(
        sudo.payg_service,"get_admin_account",AsyncMock(side_effect=OSError("db down")),
    )
    remote_activation=AsyncMock()
    monkeypatch.setattr(sudo.rebecca_api,"enable_admin",remote_activation)
    local_activation=AsyncMock()
    monkeypatch.setattr(sudo.db,"reactivate_admin",local_activation)
    cb=_callback()
    cb.data="activate_panel_77"
    asyncio.run(sudo.activate_panel_selected(cb))
    local_activation.assert_not_awaited()
    remote_activation.assert_not_awaited()
    assert cb.answer.await_args.kwargs["show_alert"] is True
