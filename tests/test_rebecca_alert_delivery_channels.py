"""Regression: Rebecca notifications must track delivery for each destination."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import rebecca_admin_ops as ops


def _setup(monkeypatch, *, private_failures=0, group_failures=0):
    states = {}
    deliveries = {"private": 0, "group": 0}
    fail_private = [private_failures]
    fail_group = [group_failures]

    async def get_state(admin_id, metric):
        return states.get((int(admin_id), metric))

    async def mark_state(admin_id, metric, value, rank):
        states[(int(admin_id), metric)] = {"last_value": value, "last_rank": rank}

    async def send_private(*args, **kwargs):
        deliveries["private"] += 1
        if fail_private[0]:
            fail_private[0] -= 1
            raise RuntimeError("Telegram private delivery unavailable")

    async def send_group(*args, **kwargs):
        deliveries["group"] += 1
        if fail_group[0]:
            fail_group[0] -= 1
            return False
        return True

    monkeypatch.setattr(ops.group_ops_service, "get_alert_state", get_state)
    monkeypatch.setattr(ops.group_ops_service, "mark_alert_state", mark_state)
    monkeypatch.setattr(ops.group_ops_service, "send_text", send_group)
    bot = SimpleNamespace(send_message=send_private)
    admin = SimpleNamespace(id=7, user_id=123456789, admin_name="Panel", marzban_username="panel_1")
    return bot, admin, states, deliveries


def test_group_success_does_not_erase_failed_private_traffic_alert(monkeypatch):
    bot, admin, states, attempts = _setup(monkeypatch, private_failures=1)

    async def run():
        await ops._maybe_percent_alert(bot, admin, "traffic", 90, "Traffic", "90%")
        assert ("7", "traffic:private") not in states
        assert (7, "traffic:private") not in states
        assert states[(7, "traffic:group")]["last_rank"] == 2
        await ops._maybe_percent_alert(bot, admin, "traffic", 90, "Traffic", "90%")
        assert states[(7, "traffic:private")]["last_rank"] == 2
        assert states[(7, "traffic:group")]["last_rank"] == 2

    asyncio.run(run())
    assert attempts == {"private": 2, "group": 1}


def test_private_success_does_not_erase_failed_group_status_alert(monkeypatch):
    bot, admin, states, attempts = _setup(monkeypatch, group_failures=1)

    async def run():
        await ops._maybe_status_alert(bot, admin, "disabled")
        assert states[(7, "remote_status:private")]["last_rank"] == 1
        assert (7, "remote_status:group") not in states
        await ops._maybe_status_alert(bot, admin, "disabled")
        assert states[(7, "remote_status:group")]["last_rank"] == 1
        await ops._maybe_status_alert(bot, admin, "disabled")

    asyncio.run(run())
    assert attempts == {"private": 1, "group": 2}


def test_active_baseline_is_silent_for_both_recipients(monkeypatch):
    bot, admin, states, attempts = _setup(monkeypatch)

    asyncio.run(ops._maybe_status_alert(bot, admin, "active"))
    assert attempts == {"private": 0, "group": 0}
    assert states[(7, "remote_status:private")]["last_rank"] == 0
    assert states[(7, "remote_status:group")]["last_rank"] == 0


def test_group_success_does_not_erase_failed_private_time_alert(monkeypatch):
    bot, admin, states, attempts = _setup(monkeypatch, private_failures=1)

    async def run():
        await ops._maybe_time_alert(bot, admin, 3600, "1 hour")
        assert (7, "time:private") not in states
        assert states[(7, "time:group")]["last_rank"] > 0
        await ops._maybe_time_alert(bot, admin, 3600, "1 hour")
        assert states[(7, "time:private")]["last_rank"] > 0

    asyncio.run(run())
    assert attempts == {"private": 2, "group": 1}
