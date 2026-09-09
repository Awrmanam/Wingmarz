import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.types import InlineKeyboardButton

from handlers import panel_experience as panel


def run(coro):
    return asyncio.run(coro)


async def fake_button(text, callback_data, **kwargs):
    return InlineKeyboardButton(text=text, callback_data=callback_data)


def test_renew_entry_uses_canonical_panel_name_not_provider_identity(monkeypatch):
    monkeypatch.setattr(panel, "_button", fake_button)
    admin = SimpleNamespace(id=11, user_id=42, is_active=True, admin_name="customer_instance", origin_plan_id=7)
    monkeypatch.setattr(panel.db, "get_admins_for_user", AsyncMock(return_value=[admin]))
    monkeypatch.setattr(panel, "_admin_panel_identity", AsyncMock(return_value=("WireGuard VIP", "wire")))
    message = SimpleNamespace(edit_text=AsyncMock())
    callback = SimpleNamespace(
        from_user=SimpleNamespace(id=42),
        message=message,
        answer=AsyncMock(),
    )
    state = SimpleNamespace(clear=AsyncMock())

    run(panel.canonical_renew_entry(callback, state))

    rows = message.edit_text.call_args.kwargs["reply_markup"].inline_keyboard
    assert rows[0][0].text == "WireGuard VIP"
    assert rows[0][0].callback_data == "admin_renew_panel_11"
    assert "customer_instance" not in rows[0][0].text


def test_duplicate_panel_family_keeps_instances_distinguishable(monkeypatch):
    monkeypatch.setattr(panel, "_button", fake_button)
    admins = [
        SimpleNamespace(id=1, user_id=42, is_active=True, admin_name="alpha", origin_plan_id=7),
        SimpleNamespace(id=2, user_id=42, is_active=True, admin_name="beta", origin_plan_id=8),
    ]
    monkeypatch.setattr(panel.db, "get_admins_for_user", AsyncMock(return_value=admins))
    monkeypatch.setattr(panel, "_admin_panel_identity", AsyncMock(return_value=("WireGuard", "wire")))
    message = SimpleNamespace(edit_text=AsyncMock())
    callback = SimpleNamespace(from_user=SimpleNamespace(id=42), message=message, answer=AsyncMock())
    state = SimpleNamespace(clear=AsyncMock())

    run(panel.canonical_renew_entry(callback, state))
    labels = [row[0].text for row in message.edit_text.call_args.kwargs["reply_markup"].inline_keyboard[:-1]]
    assert labels == ["WireGuard · alpha", "WireGuard · beta"]


def test_full_renew_final_order_callback_is_not_intercepted():
    source = Path("handlers/panel_experience.py").read_text(encoding="utf-8")
    assert '~F.data.startswith("admin_full_renew_plan_")' in source


def test_panel_experience_router_is_before_legacy_admin_router():
    source = Path("handlers/__init__.py").read_text(encoding="utf-8")
    assert "include_router(panel_experience_router)" in source
