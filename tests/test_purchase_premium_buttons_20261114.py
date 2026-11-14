"""Purchase buttons use registered Premium Emoji keys without changing routing."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.types import InlineKeyboardButton

from handlers import payg_offers, plan_storefront


def run(coro):
    return asyncio.run(coro)


async def button_capture(calls, text, callback_data=None, **kwargs):
    calls.append((text, callback_data, kwargs.get("icon_key"), kwargs.get("fallback")))
    return InlineKeyboardButton(text=text, callback_data=callback_data)


def test_billing_purchase_buttons_are_style_aware(monkeypatch):
    calls = []
    monkeypatch.setattr(
        payg_offers.style_engine, "styled_button",
        lambda text, callback_data=None, **kwargs: button_capture(
            calls, text, callback_data, **kwargs
        ),
    )
    monkeypatch.setattr(
        payg_offers, "_purchase_availability", AsyncMock(return_value=(1, 1))
    )
    msg = SimpleNamespace(edit_text=AsyncMock())
    run(payg_offers.render_purchase_method(msg, "p"))
    assert [(callback, key) for _, callback, key, _ in calls] == [
        ("buyflow:payg:p", "payg"),
        ("planmarket:root:p", "plan"),
        ("panelprov:root:p", "back"),
    ]
    assert all(not text.startswith(("⚡", "📦", "🔙")) for text, *_ in calls)


def test_payg_service_buttons_have_icon_and_preserve_prices(monkeypatch):
    calls = []
    monkeypatch.setattr(
        payg_offers.style_engine, "styled_button",
        lambda text, callback_data=None, **kwargs: button_capture(
            calls, text, callback_data, **kwargs
        ),
    )
    monkeypatch.setattr(
        payg_offers.payg_offer_service, "list_offers",
        AsyncMock(return_value=[{"service_id": 15, "price_per_gb_toman": 2500}]),
    )
    monkeypatch.setattr(
        payg_offers, "get_service_by_rebecca_id",
        AsyncMock(return_value=SimpleNamespace(
            display_name="Promax", is_enabled=True,
        )),
    )
    msg = SimpleNamespace(edit_text=AsyncMock())
    run(payg_offers.render_payg_storefront(msg, "p"))
    assert calls[0] == ("Promax · 2,500 ت/GB", "paygsvc:offer:p:15", "payg", "⚡")
    assert calls[1][1:] == ("buyflow:method:p", "back", "🔙")


def test_fixed_duration_buttons_use_registered_calendar_key(monkeypatch):
    calls = []
    monkeypatch.setattr(
        plan_storefront, "_button",
        lambda text, callback_data, **kwargs: button_capture(
            calls, text, callback_data, **kwargs
        ),
    )
    category = SimpleNamespace(
        id=10, name="Promax", is_active=True, provider_enabled=True,
        description="", rebecca_service_id=15,
    )
    plans = [
        SimpleNamespace(id=1, time_limit_seconds=30*86400),
        SimpleNamespace(id=2, time_limit_seconds=60*86400),
    ]
    monkeypatch.setattr(
        plan_storefront.product_catalog, "get_category",
        AsyncMock(return_value=category),
    )
    monkeypatch.setattr(
        plan_storefront.product_catalog, "plans_for_category",
        AsyncMock(return_value=plans),
    )
    monkeypatch.setattr(
        plan_storefront.service_marketplace_service,
        "duration_groups_enabled", AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        plan_storefront.service_marketplace_service,
        "group_plans_by_duration",
        lambda plans: [
            SimpleNamespace(key="30d", label="1 ماهه"),
            SimpleNamespace(key="60d", label="2 ماهه"),
        ],
    )
    monkeypatch.setattr(
        plan_storefront, "_panel_heading", AsyncMock(return_value="<b>Promax</b>"),
    )
    monkeypatch.setattr(
        plan_storefront, "purchase_message",
        AsyncMock(return_value="<b>Promax</b>\nانتخاب مدت"),
    )
    msg = SimpleNamespace(edit_text=AsyncMock())
    run(plan_storefront._render_category(msg, "p", 10))
    assert len(calls) == 3
    assert [item[2] for item in calls[:2]] == ["calendar", "calendar"]
    assert [item[1] for item in calls[:2]] == [
        "planmarket:d:p:10:30d", "planmarket:d:p:10:60d",
    ]
