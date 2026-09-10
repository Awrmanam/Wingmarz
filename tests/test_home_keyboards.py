import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from handlers import home_keyboards
from handlers import home_navigation


def run(coro):
    return asyncio.run(coro)


def test_public_home_keyboard_uses_runtime_style_engine(monkeypatch):
    calls = []

    async def fake_styled(text, **kwargs):
        calls.append((text, kwargs))
        return SimpleNamespace(text=text, callback_data=kwargs.get("callback_data"))

    monkeypatch.setattr(home_keyboards.style_engine, "styled_button", fake_styled)
    keyboard = run(home_keyboards.public_home_keyboard())

    assert [row[0].callback_data for row in keyboard.inline_keyboard] == [
        "public_buy_reseller",
        "svcmarket:trial",
        "support:home",
    ]
    assert all("callback_data" in kwargs for _text, kwargs in calls)


def test_reseller_home_keyboard_preserves_business_callbacks(monkeypatch):
    async def fake_styled(text, **kwargs):
        return SimpleNamespace(text=text, callback_data=kwargs.get("callback_data"))

    monkeypatch.setattr(home_keyboards.style_engine, "styled_button", fake_styled)
    keyboard = run(home_keyboards.reseller_home_keyboard())
    callbacks = [button.callback_data for row in keyboard.inline_keyboard for button in row]

    assert callbacks == [
        "my_info",
        "my_report",
        "my_users",
        "reactivate_users",
        "admin_buy_reseller",
        "admin_renew",
        "svcmarket:trial",
        "support:home",
    ]


def test_home_renderer_no_longer_uses_legacy_direct_keyboard_builders():
    source = open("handlers/home_navigation.py", encoding="utf-8").read()
    assert "get_admin_keyboard" not in source
    assert "get_public_main_keyboard" not in source
    assert "await reseller_home_keyboard()" in source
    assert "await public_home_keyboard()" in source
