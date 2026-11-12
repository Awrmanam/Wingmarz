"""Durable Rebecca alerts must be scoped at send-time, not only scan-time."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiosqlite

import config
import rebecca_user_alerts as alerts
from rebecca_user_alerts import RebeccaUserAlerts


USER = {
    "username": "private_user",
    "status": "active",
    "data_limit": 100,
    "used_traffic": 0,
    "admin": "panel-a",
}


def test_queued_alert_does_not_leak_after_panel_owner_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "REBECCA_USER_ALERTS_GROUP_WINDOW", 0)
    svc = RebeccaUserAlerts(str(tmp_path / "queue.sqlite"))
    bot = SimpleNamespace(send_message=AsyncMock())

    async def scenario():
        await svc.observe_panel("panel-a", 42, [USER], now=1000)
        await svc.observe_panel(
            "panel-a", 42, [{**USER, "used_traffic": 100}], now=1100,
        )
        assert await svc.deliver(
            bot, now=1100, owner_map={"panel-a": 999},
        ) == 0
        bot.send_message.assert_not_awaited()
        async with aiosqlite.connect(svc.db_path) as conn:
            async with conn.execute(
                "SELECT delivery_state,attempts FROM rebecca_user_alert_events"
            ) as cur:
                assert await cur.fetchone() == ("pending", 1)
        # A scheduled retry must not silently hand this private event
        # to the new owner either.
        assert await svc.deliver(bot, now=1160, owner_map={"panel-a": 999}) == 0
        bot.send_message.assert_not_awaited()

    asyncio.run(scenario())


def test_telegram_outbox_drains_when_rebecca_is_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PANEL_PROVIDER", "rebecca")
    monkeypatch.setattr(config, "REBECCA_USER_ALERTS_GROUP_WINDOW", 0)
    svc = RebeccaUserAlerts(str(tmp_path / "offline.sqlite"))
    owner = SimpleNamespace(
        marzban_username="panel-a", user_id=42, is_active=True,
    )
    monkeypatch.setattr(alerts.db, "get_all_admins",
                        AsyncMock(return_value=[owner]))
    monkeypatch.setattr(
        alerts.rebecca_api, "list_admins",
        AsyncMock(side_effect=RuntimeError("Rebecca offline")),
    )
    bot = SimpleNamespace(send_message=AsyncMock())

    async def scenario():
        await svc.observe_panel("panel-a", 42, [USER], now=1000)
        await svc.observe_panel(
            "panel-a", 42, [{**USER, "used_traffic": 100}], now=1100,
        )
        result = await svc.tick(bot, now=1200)
        assert result == {"polled": 0, "events": 0, "delivered": 1}
        bot.send_message.assert_awaited_once()
        assert bot.send_message.await_args.args[0] == 42

    asyncio.run(scenario())
