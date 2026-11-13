"""Rebecca user quota alerts: full pagination, idempotence, privacy and grouping."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiosqlite
import pytest

import config
import rebecca_user_alerts as alerts
from rebecca_user_alerts import RebeccaUserAlerts, classify_user


def run(coro):
    return asyncio.run(coro)


def user(username="alice", *, used=50, limit=100, status="active", expire=None):
    return {
        "username": username, "status": status,
        "used_traffic": used, "data_limit": limit, "expire": expire,
    }


async def rows(path, sql):
    async with aiosqlite.connect(path) as db:
        return await (await db.execute(sql)).fetchall()


def test_quota_expiry_and_manual_disable_have_distinct_meanings():
    assert classify_user(user(used=100, limit=100), 2000) == "quota_full"
    assert classify_user(user(used=100, limit=100, status="disabled"), 2000) == "disabled"
    assert classify_user(user(used=100, limit=100, expire=1900), 2000) == "expired"
    assert classify_user(user(status="limited", used=0, limit=None), 2000) == "limited"
    assert classify_user(user(used=100, limit=None), 2000) == "active"
    assert classify_user(user(status="quota_full", used=0, limit=None), 2000) == "quota_full"


def test_initial_exhausted_baseline_and_one_alert_per_real_new_episode(tmp_path):
    path = tmp_path / "db.sqlite"
    svc = RebeccaUserAlerts(str(path))

    async def scenario():
        # Existing limited users do not spam when monitoring is enabled.
        assert await svc.observe_panel(
            "panel-a", 42, [user(used=100, limit=100)], now=1000
        ) == 0
        assert await svc.observe_panel(
            "panel-a", 42, [user(used=100, limit=100)], now=1001
        ) == 0
        assert await svc.observe_panel(
            "panel-a", 42, [user(used=5, limit=100)], now=1100
        ) == 0
        assert await svc.observe_panel(
            "panel-a", 42, [user(used=100, limit=100)], now=1200
        ) == 1
        assert await svc.observe_panel(
            "panel-a", 42, [user(used=100, limit=100)], now=1300
        ) == 0
        assert await svc.observe_panel(
            "panel-a", 42, [user(used=1, limit=200)], now=1400
        ) == 0
        assert await svc.observe_panel(
            "panel-a", 42, [user(used=200, limit=200)], now=1500
        ) == 1

    run(scenario())
    events = run(rows(path, "SELECT episode,event_type FROM rebecca_user_alert_events ORDER BY id"))
    assert events == [(1, "quota_full"), (2, "quota_full")]


def test_changed_telegram_owner_cannot_inherit_another_reseller_alerts(tmp_path):
    path = tmp_path / "db.sqlite"
    svc = RebeccaUserAlerts(str(path))

    async def scenario():
        await svc.observe_panel("panel-a", 42, [user()], now=1000)
        with pytest.raises(ValueError, match="owner mismatch"):
            await svc.observe_panel("panel-a", 999, [user(used=100)], now=1100)

    run(scenario())
    assert run(rows(path, "SELECT owner_telegram_id,user_state FROM rebecca_user_alert_watch")) == [
        (42, "active")
    ]
    assert run(rows(path, "SELECT id FROM rebecca_user_alert_events")) == []


def test_grouped_delivery_aggregates_by_owner_and_respects_window(tmp_path, monkeypatch):
    path = tmp_path / "db.sqlite"
    svc = RebeccaUserAlerts(str(path))
    monkeypatch.setattr(config, "REBECCA_USER_ALERTS_GROUP_WINDOW", 60)
    monkeypatch.setattr(config, "REBECCA_USER_ALERTS_GROUP_MIN", 3)
    bot = SimpleNamespace(send_message=AsyncMock())

    async def scenario():
        baseline = [user(f"u{i}") for i in range(5)]
        await svc.observe_panel("panel-a", 42, baseline, now=1000)
        await svc.observe_panel("panel-a", 42, [
            user(f"u{i}", used=100) for i in range(5)
        ], now=1100)
        assert await svc.deliver(bot, now=1159, owner_map={"panel-a": 42}) == 0
        assert await svc.deliver(bot, now=1160, owner_map={"panel-a": 42}) == 5
        assert await svc.deliver(bot, now=1170, owner_map={"panel-a": 42}) == 0

    run(scenario())
    bot.send_message.assert_awaited_once()
    args = bot.send_message.await_args.args
    assert args[0] == 42
    assert "5 رویداد" in args[1]
    assert "u0" in args[1] and "u4" in args[1]
    assert len(run(rows(path, "SELECT id FROM rebecca_user_alert_events WHERE delivery_state='sent'"))) == 5


def test_single_alert_sends_to_correct_owner_with_escaped_username(tmp_path, monkeypatch):
    path = tmp_path / "db.sqlite"
    svc = RebeccaUserAlerts(str(path))
    monkeypatch.setattr(config, "REBECCA_USER_ALERTS_GROUP_WINDOW", 0)
    bot = SimpleNamespace(send_message=AsyncMock())

    async def scenario():
        await svc.observe_panel("panel-a", 42, [user("ali<&")], now=1000)
        await svc.observe_panel("panel-a", 42, [user("ali<&", used=100)], now=1100)
        await svc.deliver(bot, now=1100, owner_map={"panel-a": 42})

    run(scenario())
    assert bot.send_message.await_args.args[0] == 42
    assert "ali&lt;&amp;" in bot.send_message.await_args.args[1]


def test_failed_private_delivery_is_retried_without_marking_sent(tmp_path, monkeypatch):
    path = tmp_path / "db.sqlite"
    svc = RebeccaUserAlerts(str(path))
    monkeypatch.setattr(config, "REBECCA_USER_ALERTS_GROUP_WINDOW", 0)
    bot = SimpleNamespace(send_message=AsyncMock(side_effect=RuntimeError("blocked")))

    async def scenario():
        await svc.observe_panel("panel-a", 42, [user()], now=1000)
        await svc.observe_panel("panel-a", 42, [user(used=100)], now=1100)
        assert await svc.deliver(bot, now=1100, owner_map={"panel-a": 42}) == 0
        assert await svc.deliver(bot, now=1101, owner_map={"panel-a": 42}) == 0
        bot.send_message.side_effect = None
        assert await svc.deliver(bot, now=1130, owner_map={"panel-a": 42}) == 5-4

    run(scenario())
    state = run(rows(path, "SELECT delivery_state,attempts FROM rebecca_user_alert_events"))
    assert state == [("sent", 1)]
    assert bot.send_message.await_count == 2


def test_pagination_fetches_all_users_and_rejects_incomplete_reads(monkeypatch):
    requested = []
    async def fetch(_method, _path, **kwargs):
        offset = kwargs["params"]["offset"]
        requested.append(offset)
        values = [user(f"item{i}") for i in range(offset, min(450, offset+200))]
        return {"users": values, "total": 450}
    monkeypatch.setattr(alerts.rebecca_api, "_request", AsyncMock(side_effect=fetch))
    items = run(alerts._all_scoped_users("panel-a"))
    assert len(items) == 450
    assert requested == [0, 200, 400]

    async def bad(_method, _path, **kwargs):
        offset = kwargs["params"]["offset"]
        if offset:
            return {"users": [], "total": 450}
        return {"users": [user(str(i)) for i in range(200)], "total": 450}
    monkeypatch.setattr(alerts.rebecca_api, "_request", AsyncMock(side_effect=bad))
    with pytest.raises(ValueError):
        run(alerts._all_scoped_users("panel-a"))


def test_round_robin_scan_does_not_starve_large_reseller_sets(tmp_path):
    svc = RebeccaUserAlerts(str(tmp_path / "db.sqlite"))
    admins = [
        SimpleNamespace(marzban_username=f"panel{i:02d}", user_id=i+10, is_active=True)
        for i in range(5)
    ]
    first = run(svc.poll_candidates(admins, max_panels=2))
    second = run(svc.poll_candidates(admins, max_panels=2))
    assert [a.marzban_username for a in first] != [a.marzban_username for a in second]


def test_scanner_skips_remote_ownership_conflicts_before_reading_users(tmp_path, monkeypatch):
    svc = RebeccaUserAlerts(str(tmp_path / "db.sqlite"))
    monkeypatch.setattr(config, "PANEL_PROVIDER", "rebecca")
    monkeypatch.setattr(alerts.db, "get_all_admins", AsyncMock(return_value=[
        SimpleNamespace(marzban_username="panel-a", user_id=42, is_active=True)
    ]))
    monkeypatch.setattr(alerts.rebecca_api, "list_admins", AsyncMock(return_value=[
        {"username": "panel-a", "role": "standard", "telegram_id": 999}
    ]))
    read = AsyncMock(side_effect=AssertionError("unauthorized user list request"))
    monkeypatch.setattr(alerts.rebecca_api, "_request", read)
    bot = SimpleNamespace(send_message=AsyncMock())
    result = run(svc.tick(bot, now=2000))
    assert result["polled"] == 0
    read.assert_not_awaited()
    bot.send_message.assert_not_awaited()
