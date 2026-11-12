"""Rebecca transport tests; no network or production API access."""
import asyncio

import httpx
import pytest

import config
from rebecca_api import RebeccaAPI, RebeccaAPIError


def test_read_requests_retry_transient_503_but_not_mutations(monkeypatch):
    monkeypatch.setattr(config, "REBECCA_URL", "https://rebecca.example")
    monkeypatch.setattr(config, "REBECCA_BEARER_TOKEN", "test-secret")
    seen = []

    async def handler(request):
        seen.append((request.method, request.url.path))
        if request.method == "GET":
            if len([m for m, _ in seen if m == "GET"]) < 3:
                return httpx.Response(503, json={"detail": "temporary"})
            return httpx.Response(200, json={"usage": 17})
        return httpx.Response(503, json={"detail": "temporary"})

    transport, original_client = httpx.MockTransport(handler), httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient",
        lambda *args, **kwargs: original_client(
            *args, transport=transport, **kwargs,
        ),
    )
    api = RebeccaAPI()
    assert asyncio.run(api.get_admin_usage("owner_a")) == 17
    assert len([x for x in seen if x[0] == "GET"]) == 3
    with pytest.raises(RebeccaAPIError) as exc:
        asyncio.run(api.disable_admin("owner_a", "financial hold"))
    assert exc.value.status_code == 503
    assert len([x for x in seen if x[0] == "POST"]) == 1


def test_pooled_client_lifecycle_reuses_one_client(monkeypatch):
    monkeypatch.setattr(config, "REBECCA_URL", "https://rebecca.example")
    monkeypatch.setattr(config, "REBECCA_BEARER_TOKEN", "test-secret")
    instances = []
    requests = []

    async def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"admins": []})

    transport, original_client = httpx.MockTransport(handler), httpx.AsyncClient

    def client(*args, **kwargs):
        made = original_client(*args, transport=transport, **kwargs)
        instances.append(made)
        return made

    monkeypatch.setattr(httpx, "AsyncClient", client)
    api = RebeccaAPI()

    async def scenario():
        await api.open(max_connections=3)
        initial = api._client
        await api.open(max_connections=10)
        assert api._client is initial
        assert await api.list_admins() == []
        assert await api.list_admins() == []
        assert len(instances) == 1
        assert len(requests) == 2
        assert all(request.headers["authorization"] == "Bearer test-secret"
                   for request in requests)
        await api.close()
        assert initial.is_closed
        assert api._client is None
        await api.close()

    asyncio.run(scenario())


def test_provider_timeout_never_exposes_access_token(monkeypatch):
    monkeypatch.setattr(config, "REBECCA_URL", "https://rebecca.example")
    monkeypatch.setattr(config, "REBECCA_BEARER_TOKEN", "do-not-leak-me")

    async def handler(request):
        raise httpx.ReadTimeout("do-not-leak-me")

    transport, original_client = httpx.MockTransport(handler), httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient",
        lambda *args, **kwargs: original_client(
            *args, transport=transport, **kwargs,
        ),
    )
    with pytest.raises(RebeccaAPIError) as error:
        asyncio.run(RebeccaAPI().get_admin_usage("owner_a"))
    assert "do-not-leak-me" not in str(error.value)
