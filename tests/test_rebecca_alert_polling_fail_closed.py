"""Regression checks for safe Rebecca alert polling and classification.

All provider calls are mocked: these tests never contact a live panel or
modify production state.
"""
import asyncio
from unittest.mock import AsyncMock

import pytest

import rebecca_user_alerts as alerts


def run(coro):
    return asyncio.run(coro)


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {"users": None, "total": 1},
        {"users": ["not a user"], "total": 1},
        {"users": [], "total": True},
        {"users": [], "total": -1},
        {"users": [], "total": "0"},
    ],
)
def test_rebecca_malformed_user_page_fails_closed(monkeypatch, payload):
    request = AsyncMock(return_value=payload)
    monkeypatch.setattr(alerts.rebecca_api, "_request", request)

    with pytest.raises(ValueError):
        run(alerts._all_scoped_users("reseller-a"))

    request.assert_awaited_once()


def test_rebecca_rejects_cross_reseller_user_in_first_page(monkeypatch):
    request = AsyncMock(return_value={
        "users": [
            {"username": "alice", "admin": "reseller-a"},
            {"username": "bob", "admin": "reseller-b"},
        ],
        "total": 2,
    })
    monkeypatch.setattr(alerts.rebecca_api, "_request", request)

    with pytest.raises(ValueError, match="outside requested admin"):
        run(alerts._all_scoped_users("reseller-a"))


def test_rebecca_rejects_total_change_between_pages(monkeypatch):
    async def request(_method, _path, **kwargs):
        offset = kwargs["params"]["offset"]
        if offset == 0:
            return {"users": [{"username": str(i), "admin": "a"} for i in range(200)], "total": 201}
        return {"users": [{"username": "last", "admin": "a"}], "total": 202}

    monkeypatch.setattr(alerts.rebecca_api, "_request", AsyncMock(side_effect=request))
    with pytest.raises(ValueError, match="count changed"):
        run(alerts._all_scoped_users("a"))


def test_rebecca_rejects_user_count_over_scan_cap(monkeypatch):
    request = AsyncMock(return_value={
        "users": [], "total": alerts.MAX_USERS_PER_PANEL + 1,
    })
    monkeypatch.setattr(alerts.rebecca_api, "_request", request)

    with pytest.raises(ValueError, match="safe scan cap"):
        run(alerts._all_scoped_users("a"))


@pytest.mark.parametrize(
    "user,expected",
    [
        ({"status": "disabled", "used_traffic": 100, "data_limit": 100}, "disabled"),
        ({"status": "on_hold", "used_traffic": 100, "data_limit": 100}, "on_hold"),
        ({"status": "active", "expire": 1999, "used_traffic": 100, "data_limit": 100}, "expired"),
        ({"status": "active", "used_traffic": 99, "data_limit": 100}, "active"),
        ({"status": "active", "used_traffic": 100, "data_limit": 100}, "quota_full"),
        ({"status": "active", "used_traffic": -1, "data_limit": 100}, "active"),
        ({"status": "active", "used_traffic": 100, "data_limit": 0}, "active"),
        ({"status": "active", "used_traffic": 100, "data_limit": None}, "active"),
    ],
)
def test_rebecca_state_priorities_and_invalid_quota(user, expected):
    assert alerts.classify_user(user, now=2000) == expected


@pytest.mark.parametrize(
    "users",
    [
        [{"username": "same", "admin": "a"}, {"username": "same", "admin": "a"}],
        [{"username": "", "admin": "a"}],
        [{"username": None, "admin": "a"}],
        [{"admin": "a"}],
    ],
)
def test_rebecca_rejects_duplicate_or_missing_username(monkeypatch, users):
    monkeypatch.setattr(
        alerts.rebecca_api,
        "_request",
        AsyncMock(return_value={"users": users, "total": len(users)}),
    )
    with pytest.raises(ValueError, match="username"):
        run(alerts._all_scoped_users("a"))
