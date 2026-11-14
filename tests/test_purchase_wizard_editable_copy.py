"""Editable Rebecca buyer wizard: presentation changes must not alter routing."""
from __future__ import annotations

import asyncio
from html import escape
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.types import InlineKeyboardButton

import config
from handlers import plan_storefront, service_marketplace, payg_offers
from handlers.ui_editor_v2 import _message_category
from premium_ui_service import premium_ui_service
from purchase_ui_text import purchase_message
from text_templates import fields


def run(coro):
    return asyncio.run(coro)


def message():
    return SimpleNamespace(edit_text=AsyncMock())


async def button(label, callback_data, **_kwargs):
    return InlineKeyboardButton(text=label, callback_data=callback_data)


def callbacks(msg):
    return [
        item.callback_data
        for row in msg.edit_text.call_args.kwargs["reply_markup"].inline_keyboard
        for item in row
    ]


def test_purchase_wizard_messages_are_registered_in_sales_editor():
    expected_fields = {
        "sales_purchase_provider": set(),
        "sales_purchase_method": set(),
        "sales_purchase_fixed_services": set(),
        "sales_purchase_payg_services": set(),
        "sales_purchase_duration": {"service", "description"},
        "sales_purchase_plan_list": {"service", "duration"},
    }
    from handlers.ui_editor_v2 import _message_title

    for key, required in expected_fields.items():
        assert key in config.MESSAGES
        assert _message_category(key) == "sales"
        assert _message_title(key) != "متن عمومی"
        assert fields(config.MESSAGES[key]) == required


def test_purchase_template_substitutes_dynamic_values_and_custom_emoji(monkeypatch):
    monkeypatch.setitem(
        config.MESSAGES,
        "sales_purchase_duration",
        "{emoji:buy} <b>{service}</b>{description}",
    )
    monkeypatch.setattr(
        premium_ui_service,
        "render_placeholders",
        AsyncMock(side_effect=lambda value: value.replace("{emoji:buy}", "🛍️")),
    )
    result = run(
        purchase_message(
            "sales_purchase_duration",
            service=escape("OpenVPN & More"),
            description="\n\nفعال",
        )
    )
    assert result == "🛍️ <b>OpenVPN &amp; More</b>\n\nفعال"


def test_provider_copy_override_preserves_provider_callbacks(monkeypatch):
    monkeypatch.setattr(service_marketplace, "_button", button)
    monkeypatch.setattr(
        service_marketplace,
        "list_panel_providers",
        lambda: [
            SimpleNamespace(key="rebecca", label="Rebecca", implemented=True),
            SimpleNamespace(key="sanaei", label="Sanaei", implemented=False),
        ],
    )
    mock_copy = AsyncMock(return_value="<b>انتخاب پنل سفارشی</b>")
    monkeypatch.setattr(service_marketplace, "purchase_message", mock_copy)
    msg = message()
    run(service_marketplace._render_provider_selection(msg, "p"))
    assert msg.edit_text.call_args.args[0] == "<b>انتخاب پنل سفارشی</b>"
    assert callbacks(msg) == [
        "panelprov:select:p:rebecca",
        "panelprov:select:p:sanaei",
        "public_back_main",
    ]
    mock_copy.assert_awaited_once_with("sales_purchase_provider")


def test_payg_method_copy_override_preserves_billing_choices(monkeypatch):
    monkeypatch.setattr(payg_offers, "_purchase_availability", AsyncMock(return_value=(1, 1)))
    mock_copy = AsyncMock(return_value="<b>روش خرید سفارشی</b>")
    monkeypatch.setattr(payg_offers, "purchase_message", mock_copy)
    msg = message()
    run(payg_offers.render_purchase_method(msg, "a"))
    assert msg.edit_text.call_args.args[0] == "<b>روش خرید سفارشی</b>"
    assert callbacks(msg) == ["buyflow:payg:a", "planmarket:root:a", "panelprov:root:a"]
    mock_copy.assert_awaited_once_with("sales_purchase_method")


def test_payg_service_copy_override_preserves_available_services(monkeypatch):
    monkeypatch.setattr(
        payg_offers.payg_offer_service,
        "list_offers",
        AsyncMock(return_value=[{"service_id": 7, "price_per_gb_toman": 2500}]),
    )
    monkeypatch.setattr(
        payg_offers,
        "get_service_by_rebecca_id",
        AsyncMock(return_value=SimpleNamespace(display_name="OpenVPN", is_enabled=True)),
    )
    monkeypatch.setattr(
        payg_offers,
        "purchase_message",
        AsyncMock(return_value="<b>سرویس‌های PAYG</b>"),
    )
    msg = message()
    run(payg_offers.render_payg_storefront(msg, "p"))
    assert msg.edit_text.call_args.args[0] == "<b>سرویس‌های PAYG</b>"
    assert callbacks(msg) == ["paygsvc:offer:p:7", "buyflow:method:p"]


def test_fixed_service_copy_override_preserves_catalog_ids(monkeypatch):
    category = SimpleNamespace(id=3, name="OpenVPN", is_active=True)
    monkeypatch.setattr(plan_storefront, "_button", button)
    monkeypatch.setattr(plan_storefront, "_categories", AsyncMock(return_value=[category]))
    monkeypatch.setattr(
        plan_storefront.product_catalog, "resolve_panel_icon_key", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        plan_storefront,
        "purchase_message",
        AsyncMock(return_value="<b>سرویس‌های پلن کامل</b>"),
    )
    msg = message()
    run(plan_storefront._render_storefront(msg, "p"))
    assert msg.edit_text.call_args.args[0] == "<b>سرویس‌های پلن کامل</b>"
    assert callbacks(msg) == ["planmarket:c:p:3", "buyflow:method:p"]


def test_duration_copy_preserves_escaped_category_and_picker_callbacks(monkeypatch):
    category = SimpleNamespace(
        id=3, name="OpenVPN & WireGuard", description="Fast <Safe>",
        is_active=True, provider_enabled=True,
    )
    plans = [SimpleNamespace(id=1), SimpleNamespace(id=2)]
    monkeypatch.setattr(plan_storefront, "_button", button)
    monkeypatch.setattr(plan_storefront.product_catalog, "get_category", AsyncMock(return_value=category))
    monkeypatch.setattr(plan_storefront.product_catalog, "plans_for_category", AsyncMock(return_value=plans))
    monkeypatch.setattr(plan_storefront.product_catalog, "resolve_panel_icon_key", AsyncMock(return_value=None))
    monkeypatch.setattr(plan_storefront.service_marketplace_service, "duration_groups_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(plan_storefront.service_marketplace_service, "group_plans_by_duration", lambda _plans: [
        SimpleNamespace(label="1 ماهه", key="2592000"),
        SimpleNamespace(label="3 ماهه", key="7776000"),
    ])
    copy = AsyncMock(return_value="<b>مدت انتخاب کنید</b>")
    monkeypatch.setattr(plan_storefront, "purchase_message", copy)
    msg = message()
    run(plan_storefront._render_category(msg, "a", 3))
    assert msg.edit_text.call_args.args[0] == "<b>مدت انتخاب کنید</b>"
    assert callbacks(msg) == [
        "planmarket:d:a:3:2592000", "planmarket:d:a:3:7776000", "planmarket:root:a"
    ]
    kwargs = copy.await_args.kwargs
    assert kwargs["service"] == "<b>OpenVPN &amp; WireGuard</b>"
    assert "Fast &lt;Safe&gt;" in kwargs["description"]


def test_plan_list_copy_preserves_duration_and_order_route(monkeypatch):
    category = SimpleNamespace(name="OpenVPN", is_active=True)
    plan = SimpleNamespace(id=11, name="100GB", price=250000)
    monkeypatch.setattr(plan_storefront, "_button", button)
    monkeypatch.setattr(plan_storefront.product_catalog, "get_category", AsyncMock(return_value=category))
    monkeypatch.setattr(plan_storefront.product_catalog, "resolve_panel_icon_key", AsyncMock(return_value=None))
    copy = AsyncMock(return_value="<b>پلن سفارشی</b>")
    monkeypatch.setattr(plan_storefront, "purchase_message", copy)
    msg = message()
    run(plan_storefront._render_plan_list(msg, "p", 3, [plan], duration_key="2592000", duration_label="1 ماهه"))
    assert msg.edit_text.call_args.args[0] == "<b>پلن سفارشی</b>"
    assert callbacks(msg) == [
        "planmarket:p:p:3:11:2592000", "planmarket:c:p:3"
    ]
    assert copy.await_args.kwargs["duration"] == "\n🗓 1 ماهه"
