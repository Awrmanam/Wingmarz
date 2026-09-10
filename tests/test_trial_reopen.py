import asyncio
import time
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
