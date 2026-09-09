import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.types import InlineKeyboardButton

from handlers import plan_storefront as store


def run(coro):
    return asyncio.run(coro)


async def fake_button(text, callback_data, **kwargs):
    return InlineKeyboardButton(text=text, callback_data=callback_data)


def _plan(plan_id, name, days, services="10", price=100_000):
    return SimpleNamespace(
        id=plan_id,
        name=name,
        price=price,
        time_limit_seconds=days * 86400,
        rebecca_service_ids=services,
    )


def test_paid_storefront_hides_rebecca_inbound_and_shows_plans(monkeypatch):
    monkeypatch.setattr(store, "_button", fake_button)
    monkeypatch.setattr(
        store,
        "list_services",
        AsyncMock(return_value=[SimpleNamespace(rebecca_service_id=10, display_name="Wire")]),
    )
    monkeypatch.setattr(
        store.db,
        "get_plans",
        AsyncMock(return_value=[_plan(1, "اقتصادی", 30)]),
    )
    monkeypatch.setattr(
        store.service_marketplace_service,
        "duration_groups_enabled",
        AsyncMock(return_value=True),
    )
    message = SimpleNamespace(edit_text=AsyncMock())

    run(store._render_storefront(message, "a"))

    text = message.edit_text.call_args.args[0]
    assert "Wire" not in text
    assert "پلن" in text
    rows = message.edit_text.call_args.kwargs["reply_markup"].inline_keyboard
    assert rows[0][0].callback_data == "admin_order_1"
    assert "اقتصادی" in rows[0][0].text
    assert not any(
        (button.callback_data or "").startswith("svcmarket:s:")
        for row in rows
        for button in row
    )


def test_duration_groups_stay_optional_without_exposing_inbound(monkeypatch):
    monkeypatch.setattr(store, "_button", fake_button)
    monkeypatch.setattr(
        store,
        "list_services",
        AsyncMock(return_value=[SimpleNamespace(rebecca_service_id=10, display_name="Wire")]),
    )
    monkeypatch.setattr(
        store.db,
        "get_plans",
        AsyncMock(return_value=[_plan(1, "یک ماهه", 30), _plan(2, "سه ماهه", 90)]),
    )
    monkeypatch.setattr(
        store.service_marketplace_service,
        "duration_groups_enabled",
        AsyncMock(return_value=True),
    )
    message = SimpleNamespace(edit_text=AsyncMock())

    run(store._render_storefront(message, "p"))

    text = message.edit_text.call_args.args[0]
    assert "Wire" not in text
    rows = message.edit_text.call_args.kwargs["reply_markup"].inline_keyboard
    callbacks = [button.callback_data for row in rows for button in row]
    assert "planmarket:d:p:2592000" in callbacks
    assert "planmarket:d:p:7776000" in callbacks


def test_plans_mapped_only_to_inactive_services_are_hidden(monkeypatch):
    monkeypatch.setattr(
        store,
        "list_services",
        AsyncMock(return_value=[SimpleNamespace(rebecca_service_id=10, display_name="Active")]),
    )
    monkeypatch.setattr(
        store.db,
        "get_plans",
        AsyncMock(return_value=[
            _plan(1, "فعال", 30, services="10"),
            _plan(2, "غیرفعال", 30, services="20"),
        ]),
    )

    plans = run(store._sellable_plans())
    assert [plan.id for plan in plans] == [1]
