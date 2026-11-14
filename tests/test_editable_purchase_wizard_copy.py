"""Regression checks for editable, Premium Emoji-capable buying wizard copy."""
import asyncio
from html import escape
from types import SimpleNamespace
from unittest.mock import AsyncMock

import config
from handlers import payg_offers, plan_storefront, service_marketplace, ui_editor_v2
from message_catalog import UI_MESSAGES, UI_TITLES
from premium_ui_service import premium_ui_service
from purchase_copy import purchase_copy
from text_templates import fields, validate_template


def run(coro):
    return asyncio.run(coro)


NEW_KEYS = (
    "sales_provider_step",
    "sales_billing_step",
    "sales_fixed_services_step",
    "sales_payg_services_step",
    "sales_fixed_duration_step",
    "sales_fixed_plans_step",
)


def test_all_six_copy_fields_are_registered_and_editable():
    for key in NEW_KEYS:
        assert key in UI_TITLES, key
        assert key in UI_MESSAGES, key
        assert key in config.MESSAGES, key
        assert ui_editor_v2._message_category(key) == "sales", key
        assert validate_template(UI_MESSAGES[key], UI_MESSAGES[key]) == UI_MESSAGES[key]
    assert fields(UI_MESSAGES["sales_fixed_duration_step"]) == {"service"}
    assert fields(UI_MESSAGES["sales_fixed_plans_step"]) == {"service", "duration_line"}
    for key in NEW_KEYS:
        assert "{emoji:" not in UI_MESSAGES[key], "A fresh bot must not show unresolved custom emoji tokens"


def test_template_renders_premium_emoji_without_consuming_field_placeholders(monkeypatch):
    seen = []

    async def fake_render(text):
        seen.append(text)
        return text.replace("{emoji:plan}", "💎")

    monkeypatch.setattr(premium_ui_service, "render_placeholders", fake_render)
    monkeypatch.setitem(
        config.MESSAGES, "sales_fixed_duration_step",
        "{emoji:plan} <b>انتخاب مدت</b>\n{service}",
    )
    result = run(purchase_copy("sales_fixed_duration_step", service=escape("<WireGuard>")))
    assert "💎" in result
    assert "&lt;WireGuard&gt;" in result
    assert "{emoji:plan}" in seen[0]
    assert "{service}" not in seen[0]


def test_fixed_plan_list_preserves_dynamic_service_and_duration(monkeypatch):
    async def identity(text):
        return text

    monkeypatch.setattr(premium_ui_service, "render_placeholders", identity)
    monkeypatch.setitem(
        config.MESSAGES, "sales_fixed_plans_step",
        "<b>{service}</b> | {duration_line}",
    )
    result = run(purchase_copy(
        "sales_fixed_plans_step",
        service="OpenVPN",
        duration_line="📅 ۱ ماهه",
    ))
    assert "OpenVPN" in result
    assert "۱ ماهه" in result


def test_billing_render_uses_admin_overridden_text_without_changing_callbacks(monkeypatch):
    monkeypatch.setattr(
        payg_offers.payg_offer_service,
        "list_offers",
        AsyncMock(return_value=[{"service_id": 1, "price_per_gb_toman": 50}]),
    )
    monkeypatch.setattr(
        payg_offers.service_marketplace_service,
        "sellable_services",
        AsyncMock(return_value=[(SimpleNamespace(rebecca_service_id=1), 1)]),
    )
    monkeypatch.setattr(payg_offers, "purchase_copy", AsyncMock(return_value="✨ روش خرید اختصاصی"))
    message = SimpleNamespace(edit_text=AsyncMock())
    run(payg_offers.render_purchase_method(message, "p"))
    assert message.edit_text.call_args.args[0] == "✨ روش خرید اختصاصی"
    callbacks = {
        button.callback_data for row in message.edit_text.call_args.kwargs["reply_markup"].inline_keyboard
        for button in row
    }
    assert callbacks == {"buyflow:payg:p", "planmarket:root:p", "panelprov:root:p"}


def test_provider_select_uses_editable_text_and_keeps_provider_callbacks(monkeypatch):
    monkeypatch.setattr(service_marketplace, "purchase_copy", AsyncMock(return_value="🛍️ خرید سفارشی"))
    message = SimpleNamespace(edit_text=AsyncMock())
    run(service_marketplace._render_provider_selection(message, "p"))
    assert message.edit_text.call_args.args[0] == "🛍️ خرید سفارشی"
    callbacks = {
        button.callback_data for row in message.edit_text.call_args.kwargs["reply_markup"].inline_keyboard
        for button in row
    }
    assert "panelprov:root:p" not in callbacks
    assert "public_back_main" in callbacks
    assert any(item.startswith("panelprov:select:p:") for item in callbacks)


def test_fixed_storefront_uses_editable_step_text_without_changing_product_list(monkeypatch):
    category = SimpleNamespace(id=4, name="Promax", provider_enabled=True, is_active=True)
    monkeypatch.setattr(plan_storefront, "_categories", AsyncMock(return_value=[category]))
    monkeypatch.setattr(plan_storefront.product_catalog, "resolve_panel_icon_key", AsyncMock(return_value=None))
    monkeypatch.setattr(plan_storefront, "purchase_copy", AsyncMock(return_value="✨ سرویس را انتخاب کنید"))
    msg = SimpleNamespace(edit_text=AsyncMock())
    run(plan_storefront._render_storefront(msg, "p"))
    assert msg.edit_text.call_args.args[0] == "✨ سرویس را انتخاب کنید"
    buttons = msg.edit_text.call_args.kwargs["reply_markup"].inline_keyboard
    assert buttons[0][0].callback_data == "planmarket:c:p:4"
