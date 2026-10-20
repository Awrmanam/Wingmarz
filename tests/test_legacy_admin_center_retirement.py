import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import config
from handlers import group_ops
from handlers import sudo_handlers
from ui_presentation_registry import resolve_button


def run(coro):
    return asyncio.run(coro)


def _callback(user_id=1):
    return SimpleNamespace(
        from_user=SimpleNamespace(id=user_id),
        message=SimpleNamespace(),
        answer=AsyncMock(),
    )


def _state():
    return SimpleNamespace(clear=AsyncMock())


def test_legacy_admin_center_redirects_to_canonical_list(monkeypatch):
    monkeypatch.setattr(config, "SUDO_ADMINS", [1])
    render = AsyncMock()
    monkeypatch.setattr(group_ops, "_render_admin_list", render)

    callback = _callback()
    state = _state()
    run(sudo_handlers.sudo_manage_admins_entry(callback, state))

    state.clear.assert_awaited_once()
    render.assert_awaited_once_with(callback.message, 0)
    callback.answer.assert_awaited_once()


def test_legacy_all_admins_button_redirects_to_same_canonical_list(monkeypatch):
    monkeypatch.setattr(config, "SUDO_ADMINS", [1])
    render = AsyncMock()
    monkeypatch.setattr(group_ops, "_render_admin_list", render)

    callback = _callback()
    state = _state()
    run(sudo_handlers.manage_list_all(callback, state))

    state.clear.assert_awaited_once()
    render.assert_awaited_once_with(callback.message, 0)


def test_retired_admin_center_callbacks_are_hidden_from_button_editor():
    for callback in (
        "sudo_manage_admins",
        "manage_list_all",
        "manage_user_7072365858",
        "manage_panel_42",
    ):
        resolved = resolve_button(callback, "legacy")
        assert resolved.button is None
        assert resolved.excluded_reason
