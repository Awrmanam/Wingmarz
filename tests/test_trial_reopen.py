import asyncio
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiosqlite

import config
from handlers import trial_ui_v2 as trial
from trial_delivery import load_active_trial, load_latest_connections, save_connections


def run(coro):
    return asyncio.run(coro)


def test_active_trial_lookup_is_owner_bound_and_expires(tmp_path, monkeypatch):
    db_path = str(tmp_path / "trial.db")
    monkeypatch.setattr(config, "DATABASE_PATH", db_path)

    async def scenario():
        now = int(time.time())
        async with aiosqlite.connect(db_path) as conn:
            await conn.execute(
                """
                CREATE TABLE trial_issues (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    provider TEXT NOT NULL,
                    provider_username TEXT NOT NULL,
                    service_id INTEGER,
                    subscription_url TEXT,
                    expire_at INTEGER,
                    created_at INTEGER NOT NULL
                )
                """
            )
            await conn.executemany(
                "INSERT INTO trial_issues(user_id,provider,provider_username,service_id,subscription_url,expire_at,created_at) VALUES(?,?,?,?,?,?,?)",
                [
                    (7, "rebecca", "test_7_old", 2, "https://old.example/sub", now - 1, now - 100),
                    (7, "rebecca", "test_7_live", 2, "https://live.example/sub", now + 900, now),
                    (8, "rebecca", "test_8_live", 2, "https://other.example/sub", now + 900, now),
                ],
            )
            await conn.commit()

        active = await load_active_trial(7, provider="rebecca")
        assert active["provider_username"] == "test_7_live"
        assert active["subscription_url"] == "https://live.example/sub"
        assert await load_active_trial(999, provider="rebecca") is None

    run(scenario())


def test_private_fallback_links_can_be_reopened_only_by_owner(tmp_path, monkeypatch):
    db_path = str(tmp_path / "trial.db")
    monkeypatch.setattr(config, "DATABASE_PATH", db_path)

    async def scenario():
        expire_at = int(time.time()) + 600
        await save_connections(7, {"links": ["vless://one", "vmess://two"], "expire_at": expire_at})
        assert await load_latest_connections(7) == ["vless://one", "vmess://two"]
        assert await load_latest_connections(8) == []

    run(scenario())


def test_config_picker_reopens_active_trial_without_issuing_or_showing_picker(monkeypatch):
    active = {
        "subscription_url": "https://live.example/sub",
        "links": [],
        "expire_at": int(time.time()) + 600,
        "traffic_bytes": 1024,
    }
    monkeypatch.setattr(trial, "_active_config_result", AsyncMock(return_value=active))
    render = AsyncMock()
    picker = AsyncMock()
    monkeypatch.setattr(trial, "render_config_result", render)
    monkeypatch.setattr(trial, "_render_service_picker", picker)

    callback = SimpleNamespace(
        from_user=SimpleNamespace(id=7),
        message=SimpleNamespace(),
        answer=AsyncMock(),
    )
    state = SimpleNamespace(clear=AsyncMock())

    run(trial.trial_config_picker(callback, state))

    state.clear.assert_awaited_once()
    render.assert_awaited_once_with(callback.message, 7, active, edit=True)
    picker.assert_not_awaited()
    callback.answer.assert_awaited_once_with("تست فعال شما")


def test_config_picker_shows_service_picker_when_no_active_trial(monkeypatch):
    monkeypatch.setattr(trial, "_active_config_result", AsyncMock(return_value=None))
    render = AsyncMock()
    picker = AsyncMock()
    monkeypatch.setattr(trial, "render_config_result", render)
    monkeypatch.setattr(trial, "_render_service_picker", picker)

    callback = SimpleNamespace(
        from_user=SimpleNamespace(id=7),
        message=SimpleNamespace(),
        answer=AsyncMock(),
    )
    state = SimpleNamespace(clear=AsyncMock())

    run(trial.trial_config_picker(callback, state))

    render.assert_not_awaited()
    picker.assert_awaited_once_with(callback.message, 7, "config")


def test_direct_service_callback_is_idempotent_while_trial_is_active(monkeypatch):
    """Even an old service-picker message must not create a second Rebecca user."""
    active = {
        "subscription_url": "https://live.example/sub",
        "links": [],
        "expire_at": int(time.time()) + 600,
        "traffic_bytes": 1024,
    }
    monkeypatch.setattr(trial, "_active_config_result", AsyncMock(return_value=active))
    render = AsyncMock()
    lookup = AsyncMock()
    issue = AsyncMock()
    monkeypatch.setattr(trial, "render_config_result", render)
    monkeypatch.setattr(trial.service_marketplace_service, "get_service_by_catalog_id", lookup)
    monkeypatch.setattr(trial.service_marketplace_service, "issue_config_trial_for_service", issue)

    callback = SimpleNamespace(
        data="trialv2:cfg:5",
        from_user=SimpleNamespace(id=7),
        message=SimpleNamespace(),
        answer=AsyncMock(),
    )
    state = SimpleNamespace(clear=AsyncMock())

    run(trial.issue_config_trial(callback, state))

    state.clear.assert_awaited_once()
    render.assert_awaited_once_with(callback.message, 7, active, edit=True)
    lookup.assert_not_awaited()
    issue.assert_not_awaited()
    callback.answer.assert_awaited_once_with("تست فعال شما")


def test_trial_service_rows_use_central_panel_name_and_premium_emoji(monkeypatch):
    service = SimpleNamespace(id=3, rebecca_service_id=10, display_name="provider-facing")
    category = SimpleNamespace(
        name="WireGuard Premium",
        is_active=True,
        provider_enabled=True,
    )
    monkeypatch.setattr(
        trial.service_marketplace_service,
        "trial_services",
        AsyncMock(return_value=[service]),
    )
    monkeypatch.setattr(
        trial.product_catalog,
        "get_category_by_service",
        AsyncMock(return_value=category),
    )
    monkeypatch.setattr(
        trial.product_catalog,
        "resolve_panel_icon_key",
        AsyncMock(return_value="wire"),
    )
    button = AsyncMock(return_value="button")
    monkeypatch.setattr(trial, "_button", button)

    rows = run(trial._trial_service_rows("config"))

    assert rows == [["button"]]
    button.assert_awaited_once_with(
        "WireGuard Premium",
        "trialv2:cfg:3",
        icon_key="wire",
        fallback=None,
    )


def test_only_one_router_owns_trial_entry_callbacks():
    composition = Path("handlers/__init__.py").read_text(encoding="utf-8")
    assert "include_router(trial_ui_v2_router)" in composition
    assert "trial_category_router" not in composition
