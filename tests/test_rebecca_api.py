import asyncio
import importlib

import httpx
import pytest

import config
from rebecca_api import RebeccaAPI, RebeccaConflict
from handlers.sudo_handlers import _rebecca_username_base


def test_provider_defaults_to_marzban(monkeypatch):
    monkeypatch.delenv("PANEL_PROVIDER", raising=False)
    assert importlib.reload(config).PANEL_PROVIDER == "marzban"


def test_service_ids_are_unique_positive_integers():
    assert config._parse_rebecca_service_ids("1,2,1, 3") == (1, 2, 3)
    assert config._parse_rebecca_service_ids("0,2") == ()
    assert config._parse_rebecca_service_ids("1,nope") == ()


def test_create_admin_exact_route_payload_and_verification(monkeypatch):
    monkeypatch.setattr(config, "REBECCA_URL", "https://rebecca.example")
    monkeypatch.setattr(config, "REBECCA_BEARER_TOKEN", "root-secret")
    captured = {}

    async def handler(request):
        captured["request"] = request
        body = __import__("json").loads(request.content)
        return httpx.Response(201, json={**body, "status": "active"})

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient

    def client(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client)
    api = RebeccaAPI()
    result = asyncio.run(api.create_admin_verified(
        "arman_madani_4827", "independent-password", 42,
        data_limit=100, expire=2_000_000_000, users_limit=7, services=[1, 2],
    ))
    request = captured["request"]
    payload = __import__("json").loads(request.content)
    assert request.url.path == "/api/admin"
    assert request.method == "POST"
    assert payload == {
        "username": "arman_madani_4827", "password": "independent-password",
        "role": "standard", "telegram_id": 42, "data_limit": 100,
        "expire": 2_000_000_000, "users_limit": 7, "services": [1, 2],
    }
    assert payload["role"] not in {"reseller", "sudo", "full_access"}
    assert result["status"] == "active"


def test_unlimited_values_are_null_and_conflict_is_retryable(monkeypatch):
    monkeypatch.setattr(config, "REBECCA_URL", "https://rebecca.example")
    monkeypatch.setattr(config, "REBECCA_BEARER_TOKEN", "secret")
    seen = []

    async def handler(request):
        seen.append(__import__("json").loads(request.content))
        return httpx.Response(409)

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: real_client(transport=transport, **kw))
    with pytest.raises(RebeccaConflict):
        asyncio.run(RebeccaAPI().create_admin_verified(
            "user_9_1234", "password-long-enough", 9,
            data_limit=None, expire=None, users_limit=None, services=[9],
        ))
    assert seen[0]["data_limit"] is None
    assert seen[0]["expire"] is None
    assert seen[0]["users_limit"] is None


@pytest.mark.parametrize("telegram_username, expected", [
    ("@Arman_Madani", "arman_madani"),
    ("Bad name/!?", "bad_name"),
    (None, "user_123"),
    ("نام", "user_123"),
])
def test_safe_username_base(telegram_username, expected):
    assert _rebecca_username_base(telegram_username, 123) == expected


def test_rebecca_health_check_is_get_only(monkeypatch):
    monkeypatch.setattr(config, "REBECCA_URL", "https://rebecca.example")
    monkeypatch.setattr(config, "REBECCA_BEARER_TOKEN", "secret")
    methods = []
    async def handler(request):
        methods.append((request.method, request.url.path)); return httpx.Response(200, json={})
    transport, real_client = httpx.MockTransport(handler), httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: real_client(transport=transport, **kw))
    assert asyncio.run(RebeccaAPI().health_check())
    assert methods == [("GET", "/api/admin")]


def test_panel_health_keeps_marzban_path(monkeypatch):
    import health_check
    monkeypatch.setattr(config, "PANEL_PROVIDER", "marzban")
    called = []
    async def marzban(): called.append(1); return True, "ok"
    monkeypatch.setattr(health_check, "test_marzban_api", marzban)
    assert asyncio.run(health_check.test_panel_api()) == (True, "ok")
    assert called == [1]


def test_admin_recovery_lookup_filters_and_exact_matches(monkeypatch):
    monkeypatch.setattr(config, "REBECCA_URL", "https://rebecca.example")
    monkeypatch.setattr(config, "REBECCA_BEARER_TOKEN", "secret")
    requests = []
    async def handler(request):
        requests.append(request)
        return httpx.Response(200, json=[{"username": "other"}, {"username": "reserved_1234"}])
    transport, real_client = httpx.MockTransport(handler), httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: real_client(transport=transport, **kw))
    found = asyncio.run(RebeccaAPI().find_admin("reserved_1234"))
    assert found["username"] == "reserved_1234"
    assert requests[0].method == "GET" and requests[0].url.path == "/api/admins"
    assert requests[0].url.params["username"] == "reserved_1234"


def test_official_admin_management_routes_and_url_encoding(monkeypatch):
    monkeypatch.setattr(config, "REBECCA_URL", "https://rebecca.example")
    monkeypatch.setattr(config, "REBECCA_BEARER_TOKEN", "secret")
    requests = []

    async def handler(request):
        requests.append(request)
        if request.url.path.startswith("/api/admin/usage/"):
            return httpx.Response(200, json={"usage": {"used_traffic": 12}})
        return httpx.Response(200, json={"status": "ok"})

    transport, real_client = httpx.MockTransport(handler), httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: real_client(transport=transport, **kw))
    api = RebeccaAPI()
    username = "support/name with space"
    assert asyncio.run(api.get_admin_usage(username))["used_traffic"] == 12
    asyncio.run(api.disable_admin(username, "manual_sudo"))
    asyncio.run(api.enable_admin(username))
    assert asyncio.run(api.delete_admin(username)) is True

    encoded_path = "/api/admin/support%2Fname%20with%20space"
    assert [(r.method, r.url.raw_path.decode().split("?")[0]) for r in requests] == [
        ("GET", encoded_path.replace("/admin/support", "/admin/usage/support")),
        ("POST", f"{encoded_path}/disable"),
        ("POST", f"{encoded_path}/enable"),
        ("DELETE", encoded_path),
    ]
    assert __import__("json").loads(requests[1].content) == {"reason": "manual_sudo"}
    assert all(r.headers["authorization"] == "Bearer secret" for r in requests)
