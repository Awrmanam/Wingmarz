import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import config
from handlers import payg_entry
from handlers import payg_offers
from handlers import plan_storefront


def run(coro):
    return asyncio.run(coro)


def _message():
    return SimpleNamespace(edit_text=AsyncMock())


def _callbacks(markup):
    return [
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
        if button.callback_data
    ]


def test_purchase_method_separates_payg_from_fixed(monkeypatch):
    monkeypatch.setattr(
        payg_offers.payg_offer_service,
        "list_offers",
        AsyncMock(return_value=[{"service_id": 7, "price_per_gb_toman": 2500}]),
    )
    monkeypatch.setattr(
        payg_offers.service_marketplace_service,
        "sellable_services",
        AsyncMock(return_value=[(SimpleNamespace(rebecca_service_id=7), 4)]),
    )
    message = _message()

    run(payg_offers.render_purchase_method(message, "p"))

    text = message.edit_text.call_args.args[0]
    markup = message.edit_text.call_args.kwargs["reply_markup"]
    callbacks = _callbacks(markup)

    assert "مرحله ۲ از ۴" in text
    assert "PAYG" in text
    assert "پلن کامل" in text
    assert "OpenVPN" not in text
    assert "buyflow:payg:p" in callbacks
    assert "planmarket:root:p" in callbacks
    assert "panelprov:root:p" in callbacks


def test_payg_service_step_shows_only_payg_service_and_rate(monkeypatch):
    monkeypatch.setattr(
        payg_offers.payg_offer_service,
        "list_offers",
        AsyncMock(return_value=[
            {"service_id": 7, "price_per_gb_toman": 2500},
            {"service_id": 9, "price_per_gb_toman": 2300},
        ]),
    )

    async def service(service_id):
        names = {7: "OpenVPN", 9: "Promax"}
        return SimpleNamespace(
            id=service_id,
            rebecca_service_id=service_id,
            display_name=names[service_id],
            is_enabled=True,
        )

    monkeypatch.setattr(payg_offers, "get_service_by_rebecca_id", service)
    message = _message()

    run(payg_offers.render_payg_storefront(message, "p"))

    text = message.edit_text.call_args.args[0]
    markup = message.edit_text.call_args.kwargs["reply_markup"]
    labels = [button.text for row in markup.inline_keyboard for button in row]
    callbacks = _callbacks(markup)

    assert "مرحله ۳ از ۴" in text
    assert any("OpenVPN" in label and "2,500" in label for label in labels)
    assert any("Promax" in label and "2,300" in label for label in labels)
    assert "paygsvc:offer:p:7" in callbacks
    assert "paygsvc:offer:p:9" in callbacks
    assert "buyflow:method:p" in callbacks
    assert all("پلن" not in label for label in labels)


def test_provider_selection_opens_purchase_method(monkeypatch):
    monkeypatch.setattr(config, "PANEL_PROVIDER", "rebecca")
    render = AsyncMock()
    monkeypatch.setattr(payg_offers, "render_purchase_method", render)
    message = _message()

    run(payg_entry._render_provider_storefront(message, "p", "rebecca"))

    render.assert_awaited_once_with(message, "p")


def test_fixed_storefront_is_clean_service_picker(monkeypatch):
    category = SimpleNamespace(
        id=3,
        name="OpenVPN",
        rebecca_service_id=7,
        provider_enabled=True,
        is_active=True,
    )
    monkeypatch.setattr(plan_storefront, "_categories", AsyncMock(return_value=[category]))
    monkeypatch.setattr(
        plan_storefront.product_catalog,
        "resolve_panel_icon_key",
        AsyncMock(return_value=None),
    )

    async def button(text, callback_data, **kwargs):
        from aiogram.types import InlineKeyboardButton
        return InlineKeyboardButton(text=text, callback_data=callback_data)

    monkeypatch.setattr(plan_storefront, "_button", button)
    message = _message()

    run(plan_storefront._render_storefront(message, "p"))

    text = message.edit_text.call_args.args[0]
    markup = message.edit_text.call_args.kwargs["reply_markup"]
    labels = [button.text for row in markup.inline_keyboard for button in row]
    callbacks = _callbacks(markup)

    assert "مرحله ۳ از ۴" in text
    assert labels[0] == "OpenVPN"
    assert "پلن" not in labels[0]
    assert "📁" not in labels[0]
    assert "planmarket:c:p:3" in callbacks
    assert "buyflow:method:p" in callbacks
