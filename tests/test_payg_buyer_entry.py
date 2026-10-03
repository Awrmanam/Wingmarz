import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import config
from handlers import plan_storefront as store
from handlers import service_marketplace as marketplace


def run(coro):
    return asyncio.run(coro)


def _callback():
    return SimpleNamespace(
        message=SimpleNamespace(),
        answer=AsyncMock(),
    )


def _state():
    return SimpleNamespace(clear=AsyncMock())


def test_public_buy_entry_delegates_to_payg_aware_provider_selection(monkeypatch):
    monkeypatch.setattr(config, "PANEL_PROVIDER", "rebecca")
    render = AsyncMock()
    monkeypatch.setattr(marketplace, "_render_provider_selection", render)
    callback = _callback()
    state = _state()

    run(store.plan_storefront_public(callback, state))

    state.clear.assert_awaited_once()
    render.assert_awaited_once_with(callback.message, "p")
    callback.answer.assert_awaited_once()


def test_admin_buy_entry_delegates_to_payg_aware_provider_selection(monkeypatch):
    monkeypatch.setattr(config, "PANEL_PROVIDER", "rebecca")
    render = AsyncMock()
    monkeypatch.setattr(marketplace, "_render_provider_selection", render)
    callback = _callback()
    state = _state()

    run(store.plan_storefront_admin(callback, state))

    state.clear.assert_awaited_once()
    render.assert_awaited_once_with(callback.message, "a")
    callback.answer.assert_awaited_once()
