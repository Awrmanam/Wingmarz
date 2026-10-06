import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from handlers import rebecca_admin_sync as live


def run(coro):
    return asyncio.run(coro)


def _callback(user_id=7072365858):
    return SimpleNamespace(
        from_user=SimpleNamespace(id=user_id),
        message=SimpleNamespace(edit_text=AsyncMock()),
        answer=AsyncMock(),
    )


def _admin():
    return SimpleNamespace(
        id=7,
        user_id=7072365858,
        admin_name="PAYG Promax",
        marzban_username="payg7072365858_7",
        is_active=True,
    )


def test_admin_users_snapshot_is_explicitly_scoped_to_selected_rebecca_admin(monkeypatch):
    request = AsyncMock(return_value={
        "users": [],
        "total": 0,
        "active_total": 0,
        "online_total": 0,
        "status_breakdown": {},
    })
    monkeypatch.setattr(live.rebecca_api, "_request", request)

    result = run(live._admin_users_snapshot("payg7072365858_7", limit=20))

    assert result["total"] == 0
    request.assert_awaited_once_with(
        "GET",
        "/api/users",
        params={"admin": "payg7072365858_7", "limit": 20, "sort": "username"},
    )


def test_live_report_uses_rebecca_counts_and_admin_usage(monkeypatch):
    monkeypatch.setattr(live.rebecca_api, "find_admin", AsyncMock(return_value={
        "username": "payg7072365858_7",
        "status": "active",
        "users_limit": 10,
        "data_limit": None,
    }))
    monkeypatch.setattr(live, "_admin_users_snapshot", AsyncMock(return_value={
        "users": [{"username": "client-1", "status": "active", "is_online": True}],
        "total": 1,
        "active_total": 1,
        "online_total": 1,
        "status_breakdown": {"active": 1},
    }))
    monkeypatch.setattr(live.rebecca_api, "get_admin_usage", AsyncMock(return_value=50 * 1024**3))
    monkeypatch.setattr(live, "format_traffic_size", AsyncMock(return_value="50 GB"))
    callback = _callback()

    run(live._render_report(callback, _admin()))

    text = callback.message.edit_text.call_args.args[0]
    assert "تعداد کل کاربران: <b>1</b>" in text
    assert "کاربران فعال: <b>1</b>" in text
    assert "کاربران آنلاین: <b>1</b>" in text
    assert "مجموع ترافیک مصرفی: <b>50 GB</b>" in text
    assert "0 B" not in text


def test_live_user_list_uses_rebecca_users_not_local_marzban(monkeypatch):
    monkeypatch.setattr(live, "_admin_users_snapshot", AsyncMock(return_value={
        "users": [
            {
                "username": "openvpn-client-01",
                "status": "active",
                "used_traffic": 3 * 1024**3,
                "data_limit": 10 * 1024**3,
                "is_online": True,
                "service_name": "OpenVPN",
            }
        ],
        "total": 1,
        "active_total": 1,
        "online_total": 1,
        "status_breakdown": {"active": 1},
    }))

    async def size(value):
        return f"{value // (1024**3)} GB"

    monkeypatch.setattr(live, "format_traffic_size", size)
    callback = _callback()

    run(live._render_users(callback, _admin()))

    text = callback.message.edit_text.call_args.args[0]
    assert "openvpn-client-01" in text
    assert "OpenVPN" in text
    assert "3 GB" in text
    assert "10 GB" in text
    assert "هیچ کاربری یافت نشد" not in text


def test_live_info_uses_users_endpoint_for_counts(monkeypatch):
    monkeypatch.setattr(live.rebecca_api, "find_admin", AsyncMock(return_value={
        "username": "payg7072365858_7",
        "status": "active",
        "users_limit": 20,
        "data_limit": None,
    }))
    monkeypatch.setattr(live, "_admin_users_snapshot", AsyncMock(return_value={
        "users": [{"username": "u", "status": "active", "is_online": False}],
        "total": 1,
        "active_total": 1,
        "online_total": 0,
        "status_breakdown": {"active": 1},
    }))
    monkeypatch.setattr(live.rebecca_api, "get_admin_usage", AsyncMock(return_value=50 * 1024**3))
    monkeypatch.setattr(live, "format_traffic_size", AsyncMock(return_value="50 GB"))
    callback = _callback()

    run(live._render_info(callback, _admin()))

    text = callback.message.edit_text.call_args.args[0]
    assert "• کل: <b>1</b> / 20" in text
    assert "• فعال: <b>1</b>" in text
    assert "مصرف‌شده: <b>50 GB</b>" in text
