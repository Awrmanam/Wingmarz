import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from handlers import control_center, trial_ui_v2


def run(coro):
    return asyncio.run(coro)


def test_support_back_uses_admin_home_for_any_panel_record(monkeypatch):
    monkeypatch.setattr(
        "handlers.home_navigation.has_admin_account",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(control_center, "_button", AsyncMock(side_effect=lambda text, callback_data, **kwargs: SimpleNamespace(text=text, callback_data=callback_data)))
    kb = run(control_center.support_keyboard(7))
    assert kb.inline_keyboard[0][0].callback_data == "back_to_admin_main"


def test_trial_back_uses_admin_home_for_any_panel_record(monkeypatch):
    monkeypatch.setattr(
        "handlers.home_navigation.has_admin_account",
        AsyncMock(return_value=True),
    )
    assert run(trial_ui_v2._home_callback(7)) == "back_to_admin_main"
