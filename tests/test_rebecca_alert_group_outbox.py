"""Independent group/owner receipts for Rebecca quota notifications."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiosqlite

import config
import rebecca_user_alerts as alerts
from rebecca_user_alerts import RebeccaUserAlerts


def _user(used: int):
    return {
        "username": "client_a", "admin": "panel-a",
        "status": "active", "data_limit": 100,
        "used_traffic": used,
    }


def test_owner_and_group_delivery_recover_independently(tmp_path, monkeypatch):
    service = RebeccaUserAlerts(str(tmp_path / "events.sqlite"))
    monkeypatch.setattr(config, "REBECCA_USER_ALERTS_GROUP_WINDOW", 0)
    monkeypatch.setattr(
        alerts.group_ops_service, "get_config",
        AsyncMock(return_value={"enabled":1,"chat_id":-100123}),
    )
    group_send = AsyncMock(return_value=False)
    monkeypatch.setattr(alerts.group_ops_service, "send_text", group_send)
    bot = SimpleNamespace(send_message=AsyncMock())

    async def scenario():
        await service.observe_panel(
            "panel-a", 42, [_user(0)], now=1000,
            group_destination=(-100123,99),
        )
        await service.observe_panel(
            "panel-a", 42, [_user(100)], now=1100,
            group_destination=(-100123,99),
        )
        assert await service.deliver(
            bot, now=1100, owner_map={"panel-a":42},
        ) == 1
        bot.send_message.assert_awaited_once()
        async with aiosqlite.connect(service.db_path) as conn:
            async with conn.execute(
                "SELECT delivery_state,attempts FROM rebecca_user_alert_group_queue"
            ) as cur:
                assert await cur.fetchone() == ("pending",1)
            async with conn.execute(
                "SELECT delivery_state FROM rebecca_user_alert_events"
            ) as cur:
                assert (await cur.fetchone())[0] == "sent"

        group_send.return_value = True
        assert await service.deliver(
            bot, now=1130, owner_map={"panel-a":42},
        ) == 0  # no duplicate private message
        bot.send_message.assert_awaited_once()
        assert group_send.await_count == 2
        async with aiosqlite.connect(service.db_path) as conn:
            async with conn.execute(
                "SELECT delivery_state,delivered_at "
                "FROM rebecca_user_alert_group_queue"
            ) as cur:
                assert await cur.fetchone() == ("sent",1130)

    asyncio.run(scenario())


def test_group_rebind_cancels_stale_recipient_without_leaking(tmp_path, monkeypatch):
    service = RebeccaUserAlerts(str(tmp_path / "rebind.sqlite"))
    monkeypatch.setattr(config, "REBECCA_USER_ALERTS_GROUP_WINDOW", 0)
    monkeypatch.setattr(
        alerts.group_ops_service, "get_config",
        AsyncMock(return_value={"enabled":1,"chat_id":-100200}),
    )
    group_send = AsyncMock()
    monkeypatch.setattr(alerts.group_ops_service, "send_text", group_send)
    bot = SimpleNamespace(send_message=AsyncMock())

    async def scenario():
        await service.observe_panel("panel-a",42,[_user(0)],now=1000)
        await service.observe_panel(
            "panel-a",42,[_user(100)],now=1100,
            group_destination=(-100100,17),
        )
        await service.deliver(bot, now=1100, owner_map={"panel-a":42})
        group_send.assert_not_awaited()
        async with aiosqlite.connect(service.db_path) as conn:
            async with conn.execute(
                "SELECT delivery_state,last_error "
                "FROM rebecca_user_alert_group_queue"
            ) as cur:
                state,error = await cur.fetchone()
        assert state == "cancelled"
        assert "changed" in error

    asyncio.run(scenario())


def test_group_queue_leases_prevent_parallel_claims(tmp_path, monkeypatch):
    service = RebeccaUserAlerts(str(tmp_path / "race.sqlite"))

    async def scenario():
        await service.observe_panel("panel-a",42,[_user(0)],now=1000)
        await service.observe_panel(
            "panel-a",42,[_user(100)],now=1100,
            group_destination=(-100100,17),
        )
        results = await asyncio.gather(
            service.claim_group_due(now=1100,window=0),
            service.claim_group_due(now=1100,window=0),
        )
        sizes = sorted(len(rows) for token,rows in results)
        assert sizes == [0,1]

    asyncio.run(scenario())
