import asyncio

import aiosqlite
import pytest

import config
from rebecca_api import RebeccaAPIError, rebecca_api
from rebecca_catalog import (
    RebeccaCatalogDuplicate,
    RebeccaDiscoveryError,
    RebeccaDiscoveryNotFound,
    discover_services_for_user,
    get_service,
    import_services_atomic,
    init_catalog_table,
    list_services,
    remove_service,
    rename_service,
    service_ids_from_catalog_ids,
    set_service_enabled,
)


def run(coro):
    return asyncio.run(coro)


def test_discovery_uses_exact_read_only_user_route_and_encoded_username(monkeypatch):
    seen = []

    async def request(method, path, **kwargs):
        seen.append((method, path, kwargs))
        return {"username": "support/name with space", "service_id": 7, "service_name": "Gaming"}

    monkeypatch.setattr(rebecca_api, "_request", request)
    result = run(discover_services_for_user("  support/name with space  "))
    assert seen == [("GET", "/api/user/support%2Fname%20with%20space", {})]
    assert result == {
        "username": "support/name with space",
        "services": [{"service_id": 7, "service_name": "Gaming"}],
    }


def test_discovery_404_is_not_found(monkeypatch):
    async def request(*_args, **_kwargs):
        raise RebeccaAPIError("missing", status_code=404)

    monkeypatch.setattr(rebecca_api, "_request", request)
    with pytest.raises(RebeccaDiscoveryNotFound):
        run(discover_services_for_user("demo"))


@pytest.mark.parametrize("status", [401, 403, 500, 502])
def test_discovery_provider_failures_are_not_mapped_to_not_found(monkeypatch, status):
    async def request(*_args, **_kwargs):
        raise RebeccaAPIError("provider error", status_code=status)

    monkeypatch.setattr(rebecca_api, "_request", request)
    with pytest.raises(RebeccaAPIError) as exc:
        run(discover_services_for_user("demo"))
    assert exc.value.status_code == status


@pytest.mark.parametrize("payload", [
    [],
    None,
    {"username": "other", "service_id": 1, "service_name": "A"},
    {"username": "demo"},
    {"username": "demo", "service_id": None},
    {"username": "demo", "service_id": 0},
    {"username": "demo", "service_id": -1},
    {"username": "demo", "service_id": True},
    {"username": "demo", "service_id": "3"},
    {"username": "demo", "service_id": 3, "service_name": 9},
])
def test_discovery_rejects_malformed_or_untrusted_response(monkeypatch, payload):
    async def request(*_args, **_kwargs):
        return payload

    monkeypatch.setattr(rebecca_api, "_request", request)
    with pytest.raises(RebeccaDiscoveryError):
        run(discover_services_for_user("demo"))


def test_discovery_accepts_null_service_name(monkeypatch):
    async def request(*_args, **_kwargs):
        return {"username": "demo", "service_id": 4, "service_name": None}

    monkeypatch.setattr(rebecca_api, "_request", request)
    result = run(discover_services_for_user("demo"))
    assert result["services"] == [{"service_id": 4, "service_name": None}]


def test_catalog_import_is_atomic_and_duplicate_safe(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATABASE_PATH", str(tmp_path / "catalog.db"))
    created = run(import_services_atomic([
        {"service_id": 1, "display_name": "Direct", "provider_name": "Provider Direct"},
        {"service_id": 2, "display_name": "Gaming", "provider_name": "Provider Gaming"},
    ], source_username="trial-user"))
    assert [item.rebecca_service_id for item in created] == [1, 2]

    with pytest.raises(RebeccaCatalogDuplicate):
        run(import_services_atomic([
            {"service_id": 3, "display_name": "CDN", "provider_name": None},
            {"service_id": 2, "display_name": "Duplicate", "provider_name": None},
        ], source_username="another"))

    # Service 3 must not have leaked from the failed transaction.
    assert [item.rebecca_service_id for item in run(list_services())] == [1, 2]


def test_catalog_rename_disable_remove(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATABASE_PATH", str(tmp_path / "catalog.db"))
    service = run(import_services_atomic([
        {"service_id": 9, "display_name": "Old", "provider_name": "Official"}
    ], source_username="u"))[0]

    assert run(rename_service(service.id, "New")) is True
    assert run(get_service(service.id)).display_name == "New"

    assert run(set_service_enabled(service.id, False)) is True
    assert run(list_services(enabled_only=True)) == []
    with pytest.raises(ValueError):
        run(service_ids_from_catalog_ids([service.id]))

    assert run(remove_service(service.id)) is True
    assert run(get_service(service.id)) is None


def test_catalog_has_no_foreign_key_to_plans(monkeypatch, tmp_path):
    path = tmp_path / "catalog.db"
    monkeypatch.setattr(config, "DATABASE_PATH", str(path))
    run(init_catalog_table())

    async def foreign_keys():
        async with aiosqlite.connect(path) as conn:
            async with conn.execute("PRAGMA foreign_key_list(rebecca_services)") as cur:
                return await cur.fetchall()

    assert run(foreign_keys()) == []
