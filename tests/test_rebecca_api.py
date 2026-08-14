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
        data_limit=100, expire=2_000_000_000, users_limit=7,
    ))
    request = captured["request"]
    payload = __import__("json").loads(request.content)
    assert request.url.path == "/api/admin"
    assert request.method == "POST"
    assert payload == {
        "username": "arman_madani_4827", "password": "independent-password",
        "role": "standard", "telegram_id": 42, "data_limit": 100,
        "expire": 2_000_000_000, "users_limit": 7,
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
            data_limit=None, expire=None, users_limit=None,
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
