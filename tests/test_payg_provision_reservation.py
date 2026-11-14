"""Durable PAYG service provisioning and ambiguous Rebecca failures."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiosqlite
import pytest

import payg_offers_service as mod
from payg_service import PaygService


@pytest.mark.asyncio
async def test_concurrent_provision_only_one_remote_create(tmp_path, monkeypatch):
    path = str(tmp_path / "provision.db")
    core = PaygService(path)
    monkeypatch.setattr(mod, "payg_service", core)
    service = mod.PaygOfferService(path)
    await service.ensure_schema()
    monkeypatch.setattr(
        mod, "get_service_by_rebecca_id",
        AsyncMock(return_value=SimpleNamespace(is_enabled=True, display_name="Test")),
    )
    await service.ensure_offer(7)
    await service.update_offer(
        7, enabled=True, price_per_gb_toman=1000, min_topup_toman=1
    )
    await core.credit_wallet(42, 5000, kind="test_seed")
    monkeypatch.setattr(
        mod, "get_service_by_rebecca_id",
        AsyncMock(return_value=SimpleNamespace(is_enabled=True, display_name="Test")),
    )
    monkeypatch.setattr(mod.rebecca_api, "find_admin", AsyncMock(return_value=None))
    gate = asyncio.Event()
    calls = []

    async def delayed_create(*args, **kwargs):
        calls.append(args[0])
        await gate.wait()

    monkeypatch.setattr(mod.rebecca_api, "create_admin_verified", delayed_create)
    first = asyncio.create_task(service.provision(42, 7))
    for _ in range(100):
        await asyncio.sleep(0.005)
        if calls:
            break
    assert calls == ["payg42_7"]
    with pytest.raises(mod.PaygOfferError, match="pending reconciliation"):
        await service.provision(42, 7)
    gate.set()
    try:
        await first
    except Exception:
        # Local admin persistence may be unavailable in isolated test DB;
        # reservation must still prevent a second remote CREATE.
        pass
    assert calls == ["payg42_7"]


@pytest.mark.asyncio
async def test_remote_timeout_never_deletes_or_recreates(tmp_path, monkeypatch):
    path = str(tmp_path / "provision.db")
    core = PaygService(path)
    monkeypatch.setattr(mod, "payg_service", core)
    service = mod.PaygOfferService(path)
    await service.ensure_schema()
    monkeypatch.setattr(
        mod, "get_service_by_rebecca_id",
        AsyncMock(return_value=SimpleNamespace(is_enabled=True, display_name="Test")),
    )
    await service.ensure_offer(7)
    await service.update_offer(
        7, enabled=True, price_per_gb_toman=1000, min_topup_toman=1
    )
    await core.credit_wallet(42, 5000, kind="test_seed")
    monkeypatch.setattr(
        mod, "get_service_by_rebecca_id",
        AsyncMock(return_value=SimpleNamespace(is_enabled=True, display_name="Test")),
    )
    monkeypatch.setattr(mod.rebecca_api, "find_admin", AsyncMock(return_value=None))
    create = AsyncMock(side_effect=TimeoutError("ambiguous Rebecca timeout"))
    delete = AsyncMock()
    monkeypatch.setattr(mod.rebecca_api, "create_admin_verified", create)
    monkeypatch.setattr(mod.rebecca_api, "delete_admin", delete)
    with pytest.raises(TimeoutError):
        await service.provision(42, 7)
    with pytest.raises(mod.PaygOfferError, match="pending reconciliation"):
        await service.provision(42, 7)
    assert create.await_count == 1
    delete.assert_not_awaited()
    async with aiosqlite.connect(path) as conn:
        async with conn.execute(
            "SELECT status,remote_username FROM payg_provision_operations"
        ) as cur:
            row = await cur.fetchone()
    assert row == ("reconcile", "payg42_7")


@pytest.mark.asyncio
async def test_restart_inspects_ambiguous_operation_without_creating(tmp_path, monkeypatch):
    path = str(tmp_path / "restart.db")
    core = PaygService(path)
    monkeypatch.setattr(mod,"payg_service",core)
    service = mod.PaygOfferService(path)
    await service.ensure_schema()
    async with aiosqlite.connect(path) as conn:
        await conn.execute(
            "INSERT INTO payg_provision_operations "
            "(user_id,service_id,remote_username,remote_password,status) "
            "VALUES(42,7,'payg42_7','secret','remote_pending')"
        )
        await conn.commit()
    find = AsyncMock(return_value={"username":"payg42_7"})
    create = AsyncMock()
    monkeypatch.setattr(mod.rebecca_api,"find_admin",find)
    monkeypatch.setattr(mod.rebecca_api,"create_admin_verified",create)
    restarted = mod.PaygOfferService(path)
    results = await restarted.inspect_incomplete_provisions()
    assert results[0]["status"] == "remote_present_needs_ownership_review"
    create.assert_not_awaited()
    async with aiosqlite.connect(path) as conn:
        async with conn.execute("SELECT COUNT(*) FROM payg_provision_audit") as cur:
            assert (await cur.fetchone())[0] == 1
