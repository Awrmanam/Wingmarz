import asyncio

from aiogram.types import InlineKeyboardButton

from handlers import home_keyboards


def run(coro):
    return asyncio.run(coro)


def test_public_home_keyboard_uses_runtime_style_engine(monkeypatch):
    calls = []

    async def fake_styled(text, **kwargs):
        calls.append((text, kwargs))
        return InlineKeyboardButton(text=text, callback_data=kwargs.get("callback_data"))

    monkeypatch.setattr(home_keyboards.style_engine, "styled_button", fake_styled)
    keyboard = run(home_keyboards.public_home_keyboard())
    callbacks = [button.callback_data for row in keyboard.inline_keyboard for button in row]

    assert callbacks == [
        "public_buy_reseller",
        "tariffs:home",
        "svcmarket:trial",
        "kyc:status",
        "support:home",
    ]
    assert all("callback_data" in kwargs for _text, kwargs in calls)


def test_reseller_home_keyboard_preserves_business_callbacks(monkeypatch):
    async def fake_styled(text, **kwargs):
        return InlineKeyboardButton(text=text, callback_data=kwargs.get("callback_data"))

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
        "tariffs:home",
        "svcmarket:trial",
        "kyc:status",
        "support:home",
    ]


def test_home_renderer_no_longer_uses_legacy_direct_keyboard_builders():
    source = open("handlers/home_navigation.py", encoding="utf-8").read()
    assert "get_admin_keyboard" not in source
    assert "get_public_main_keyboard" not in source
    assert "await reseller_home_keyboard()" in source
    assert "await public_home_keyboard()" in source
    assert '@home_navigation_router.message(CommandStart(), F.chat.type == "private")' in source


def test_kyc_emoji_is_separate_from_account_info_for_both_home_roles(monkeypatch):
    calls = []

    async def fake_styled(text, **kwargs):
        calls.append((text, kwargs))
        return InlineKeyboardButton(text=text, callback_data=kwargs.get("callback_data"))

    monkeypatch.setattr(home_keyboards.style_engine, "styled_button", fake_styled)
    run(home_keyboards.public_home_keyboard())
    run(home_keyboards.reseller_home_keyboard())
    kyc = [kwargs for _text, kwargs in calls if kwargs.get("callback_data") == "kyc:status"]
    info = [kwargs for _text, kwargs in calls if kwargs.get("callback_data") == "my_info"]
    assert len(kyc) == 2
    assert all(button.get("icon_key") == "kyc" for button in kyc)
    assert len(info) == 1 and info[0]["icon_key"] == "info"


def test_shared_kyc_button_is_searchable_in_editor():
    from ui_presentation_registry import resolve_button, buttons_for_screen
    resolved = resolve_button("kyc:status", "وضعیت احراز هویت")
    assert resolved.button is not None, (
        [(x.key, x.title, x.default_text) for x in
         __import__("ui_presentation_registry")._BY_CALLBACK.get("kyc:status", [])],
        __import__("ui_presentation_registry")._clean("وضعیت احراز هویت"),
    )
    assert resolved.button.screen == "home.shared"
    assert any(button.callback_data == "kyc:status" for button in buttons_for_screen("home.shared"))
