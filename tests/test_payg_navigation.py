"""Navigation regressions: legacy PAYG buttons must never revive mixed menus."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from handlers import operations, payg as legacy_payg, payg_entry, payg_offers


def _callback(user_id=99):
    return SimpleNamespace(
        from_user=SimpleNamespace(id=user_id),
        message=SimpleNamespace(edit_text=AsyncMock()),
        answer=AsyncMock(),
    )


def _state():
    return SimpleNamespace(clear=AsyncMock())


def _button_callbacks(keyboard):
    return [button.callback_data for row in keyboard.inline_keyboard for button in row]


@pytest.mark.asyncio
async def test_stale_sales_menu_callback_uses_canonical_hub(monkeypatch):
    monkeypatch.setattr(payg_entry.config, "SUDO_ADMINS", [99])
    hub = AsyncMock()
    monkeypatch.setattr(operations, "_render_hub", hub)
    callback, state = _callback(), _state()

    await payg_entry.sales_menu_with_payg(callback, state)

    hub.assert_awaited_once_with(callback.message, "sales")
    state.clear.assert_awaited_once()
    callback.answer.assert_awaited_once_with()
    callback.message.edit_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_stale_sales_menu_denies_non_sudo(monkeypatch):
    monkeypatch.setattr(payg_entry.config, "SUDO_ADMINS", [99])
    hub = AsyncMock()
    monkeypatch.setattr(operations, "_render_hub", hub)
    callback, state = _callback(42), _state()

    await payg_entry.sales_menu_with_payg(callback, state)

    hub.assert_not_awaited()
    state.clear.assert_not_awaited()
    callback.answer.assert_awaited_once_with("غیرمجاز", show_alert=True)


@pytest.mark.asyncio
async def test_legacy_payg_home_redirects_to_service_tariffs(monkeypatch):
    monkeypatch.setattr(legacy_payg.config, "SUDO_ADMINS", [99])
    new_home = AsyncMock()
    monkeypatch.setattr(payg_offers, "render_admin_home", new_home)
    callback, state = _callback(), _state()

    await legacy_payg.payg_admin_home(callback, state)

    new_home.assert_awaited_once_with(callback.message)
    state.clear.assert_awaited_once()
    callback.answer.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_discount_and_tariff_navigation_uses_canonical_hubs(monkeypatch):
    discount_callbacks = _button_callbacks(legacy_payg._discount_menu())
    assert "paygsvcadmin:home" in discount_callbacks
    assert "paygadmin:home" not in discount_callbacks

    monkeypatch.setattr(
        payg_offers.payg_offer_service, "list_catalog_with_offers",
        AsyncMock(return_value=[]),
    )
    message = SimpleNamespace(edit_text=AsyncMock())
    await payg_offers.render_admin_home(message)

    keyboard = message.edit_text.await_args.kwargs["reply_markup"]
    callbacks = _button_callbacks(keyboard)
    assert "paygadmin:discounts" in callbacks
    assert "ops:hub:finance" in callbacks
    assert "sudo_menu_sales" not in callbacks


@pytest.mark.asyncio
async def test_provider_wide_legacy_back_points_to_finance(monkeypatch):
    monkeypatch.setattr(
        legacy_payg.payg_service, "get_settings",
        AsyncMock(return_value={
            "enabled": 0,
            "price_per_gb_toman": 0,
            "min_topup_toman": 100000,
            "max_users": 50,
            "service_ids": "1,3",
        }),
    )
    message = SimpleNamespace(edit_text=AsyncMock())
    await legacy_payg._render_admin_home(message)

    keyboard = message.edit_text.await_args.kwargs["reply_markup"]
    callbacks = _button_callbacks(keyboard)
    assert "ops:hub:finance" in callbacks
    assert "sudo_menu_sales" not in callbacks
