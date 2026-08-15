from types import SimpleNamespace

import pytest
from aiogram import Bot

import config
import handlers.sudo_handlers as sudo
from utils.bold_fix_bot import BoldFixBot
from utils.rebecca import credential_message


def sync_test(function):
    from functools import wraps
    @wraps(function)
    def wrapper(*args, **kwargs):
        import asyncio
        return asyncio.run(function(*args, **kwargs))
    return wrapper


@sync_test
async def test_boldfix_preserves_explicit_html_credentials(monkeypatch):
    captured = {}

    async def capture(self, method, *args, **kwargs):
        captured["method"] = method
        return SimpleNamespace()

    monkeypatch.setattr(Bot, "__call__", capture)
    bot = BoldFixBot("123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi")
    text = credential_message(
        "armanstore2_support_8090", "exact_p_ass&word", "https://panel.example/login", "Plan One"
    )
    try:
        await bot.send_message(42, text, parse_mode="HTML")
    finally:
        await bot.session.close()

    outgoing = captured["method"]
    assert outgoing.text == text
    assert "<code>armanstore2_support_8090</code>" in outgoing.text
    assert "&lt;code&gt;" not in outgoing.text
    assert "exact_p_ass&amp;word" in outgoing.text
    assert getattr(outgoing.parse_mode, "value", outgoing.parse_mode).lower() == "html"


class FakeState:
    def __init__(self):
        self.data = {}
        self.state = sudo.AddAdminStates.waiting_for_admin_name
        self.cleared = False

    async def get_state(self): return self.state
    async def get_data(self): return dict(self.data)
    async def update_data(self, **kwargs): self.data.update(kwargs)
    async def set_state(self, state): self.state = state
    async def clear(self): self.cleared = True; self.state = None; self.data.clear()


class FakeMessage:
    def __init__(self, text, user_id):
        self.text = text
        self.from_user = SimpleNamespace(id=user_id)
        self.answers = []

    async def answer(self, text, **kwargs):
        self.answers.append((text, kwargs))


@pytest.mark.parametrize("raw, expected", [
    ("محمد", "محمد"),
    ("  محمد رضایی  ", "محمد رضایی"),
    (" Alice Smith ", "Alice Smith"),
])
@sync_test
async def test_process_admin_name_real_handler_accepts_unicode(monkeypatch, raw, expected):
    monkeypatch.setattr(config, "SUDO_ADMINS", [99])
    state, message = FakeState(), FakeMessage(raw, 99)

    await sudo.process_admin_name(message, state)

    assert state.data["admin_name"] == expected
    assert state.state == sudo.AddAdminStates.waiting_for_marzban_username
    assert state.cleared is False
    assert len(message.answers) == 1
    assert message.answers[0][1]["parse_mode"] == "HTML"
    assert expected in message.answers[0][0]


def _button_texts(keyboard):
    return [button.text for row in keyboard.inline_keyboard for button in row]


def test_marzban_sales_menu_has_no_rebecca_service_button(monkeypatch):
    monkeypatch.setattr(config, "PANEL_PROVIDER", "marzban")
    assert "✏️ سرویس Rebecca پلن" not in _button_texts(sudo._sales_menu_keyboard())
    monkeypatch.setattr(config, "PANEL_PROVIDER", "rebecca")
    assert "✏️ سرویس Rebecca پلن" in _button_texts(sudo._sales_menu_keyboard())


class FakeCallback:
    def __init__(self, user_id=7):
        self.from_user = SimpleNamespace(id=user_id)
        self.data = "sales_edit_service_3"
        self.answers = []
        self.message = SimpleNamespace(edit_text=None)

    async def answer(self, text=None, **kwargs): self.answers.append((text, kwargs))


@sync_test
async def test_service_edit_selection_rejects_non_sudo_without_fsm_mutation(monkeypatch):
    monkeypatch.setattr(config, "PANEL_PROVIDER", "rebecca")
    monkeypatch.setattr(config, "SUDO_ADMINS", [99])
    state, callback = FakeState(), FakeCallback(7)
    original_state = state.state
    await sudo.sales_edit_service_selected(callback, state)
    assert state.data == {}
    assert state.state == original_state
    assert callback.answers[-1][1]["show_alert"] is True


@sync_test
async def test_service_edit_value_rejects_non_sudo_without_db_or_fsm_mutation(monkeypatch):
    monkeypatch.setattr(config, "PANEL_PROVIDER", "rebecca")
    monkeypatch.setattr(config, "SUDO_ADMINS", [99])
    state, message = FakeState(), FakeMessage("1,2", 7)
    state.data["edit_service_plan_id"] = 3
    called = False

    async def update_plan(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(sudo.db, "update_plan", update_plan)
    await sudo.sales_edit_service_value(message, state)
    assert called is False
    assert state.data == {"edit_service_plan_id": 3}
    assert state.cleared is False
