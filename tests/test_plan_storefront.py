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
        traffic_limit_bytes=100 * 1024**3,
        max_users=30,
        is_active=True,
        rebecca_service_ids=services,
    )


def _category():
    return SimpleNamespace(
        id=3,
        rebecca_service_id=10,
        name="WireGuard",
        description="دسته عمومی",
        is_active=True,
    )


def test_paid_storefront_shows_public_category_not_rebecca_inbound(monkeypatch):
    monkeypatch.setattr(store, "_button", fake_button)
    monkeypatch.setattr(
        store.product_catalog,
        "categories",
        AsyncMock(return_value=[_category()]),
    )
    monkeypatch.setattr(
        store.product_catalog,
        "plans_for_category",
        AsyncMock(return_value=[_plan(1, "اقتصادی", 30)]),
    )
    message = SimpleNamespace(edit_text=AsyncMock())

    run(store._render_storefront(message, "a"))

    text = message.edit_text.call_args.args[0]
    assert "Wire" not in text
    assert "نوع پنل" in text
    rows = message.edit_text.call_args.kwargs["reply_markup"].inline_keyboard
    assert rows[0][0].callback_data == "planmarket:c:a:3"
    assert "WireGuard" in rows[0][0].text


def test_duration_groups_are_optional_inside_public_category(monkeypatch):
    monkeypatch.setattr(store, "_button", fake_button)
    monkeypatch.setattr(store.product_catalog, "get_category", AsyncMock(return_value=_category()))
    monkeypatch.setattr(
        store.product_catalog,
        "plans_for_category",
        AsyncMock(return_value=[_plan(1, "یک ماهه", 30), _plan(2, "سه ماهه", 90)]),
    )
    monkeypatch.setattr(
        store.service_marketplace_service,
        "duration_groups_enabled",
        AsyncMock(return_value=True),
    )
    message = SimpleNamespace(edit_text=AsyncMock())

    run(store._render_category(message, "p", 3))

    text = message.edit_text.call_args.args[0]
    assert "WireGuard" in text
    assert "Wire</" not in text
    rows = message.edit_text.call_args.kwargs["reply_markup"].inline_keyboard
    callbacks = [button.callback_data for row in rows for button in row]
    assert "planmarket:d:p:3:2592000" in callbacks
    assert "planmarket:d:p:3:7776000" in callbacks


def test_plan_detail_keeps_provider_mapping_internal(monkeypatch):
    monkeypatch.setattr(store, "_button", fake_button)
    monkeypatch.setattr(store.product_catalog, "get_category", AsyncMock(return_value=_category()))
    monkeypatch.setattr(store.product_catalog, "plan_description", AsyncMock(return_value="پلن مناسب مصرف روزانه"))
    monkeypatch.setattr(store.db, "get_plan_by_id", AsyncMock(return_value=_plan(1, "اقتصادی", 30)))
    message = SimpleNamespace(edit_text=AsyncMock())

    run(store._render_plan_detail(message, "a", 3, 1, None))

    text = message.edit_text.call_args.args[0]
    assert "WireGuard" in text
    assert "اقتصادی" in text
    assert "100 GB" in text
    assert "30" in text or "1 ماهه" in text
    assert "10" not in text  # Rebecca service id must not leak.
    rows = message.edit_text.call_args.kwargs["reply_markup"].inline_keyboard
    assert rows[0][0].callback_data == "admin_order_1"
