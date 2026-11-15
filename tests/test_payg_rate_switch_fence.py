"""Rate switch concurrency fence and fail-closed Rebecca sampling."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest

import payg_service as module
from payg_service import GIB, PaygError, PaygService
from test_payg_historical_accrual import seed_account


@pytest.mark.asyncio
async def test_switch_blocks_scheduler_and_prices_old_usage_at_old_rate(tmp_path, monkeypatch):
    path = str(tmp_path / "rate.db")
    service = PaygService(path)
    await service.ensure_schema()
    account_id = seed_account(path, rate=1000)
    monkeypatch.setenv("PAYG_OWNER_ID", "99")
    monkeypatch.setattr(
        module.db, "get_admin_by_id",
        AsyncMock(return_value=SimpleNamespace(marzban_username="payg42")),
    )
    gate = asyncio.Event()
    entered = asyncio.Event()

    async def read_counter(_):
        entered.set()
        await gate.wait()
        return GIB

    monkeypatch.setattr(module.rebecca_api, "get_admin_usage", read_counter)
    task = asyncio.create_task(
        service.switch_rate_after_rebecca_sync(account_id, 99, 2000)
    )
    await asyncio.wait_for(entered.wait(), timeout=3)
    with pytest.raises(PaygError, match="scheduler billing paused"):
        await service.settle_usage(account_id, GIB)
    gate.set()
    await task
    assert await service.get_accrued_target(account_id) == 1000
    await service.settle_usage(account_id, 2 * GIB)
    assert await service.get_accrued_target(account_id) == 3000


@pytest.mark.asyncio
async def test_switch_rebecca_failure_keeps_old_rate(tmp_path, monkeypatch):
    path = str(tmp_path / "rate.db")
    service = PaygService(path)
    await service.ensure_schema()
    account_id = seed_account(path, rate=1000)
    monkeypatch.setenv("PAYG_OWNER_ID", "99")
    monkeypatch.setattr(
        module.db, "get_admin_by_id",
        AsyncMock(return_value=SimpleNamespace(marzban_username="payg42")),
    )
    monkeypatch.setattr(
        module.rebecca_api, "get_admin_usage",
        AsyncMock(side_effect=TimeoutError("Rebecca unavailable")),
    )
    with pytest.raises(TimeoutError):
        await service.switch_rate_after_rebecca_sync(account_id, 99, 2000)
    assert (await service.get_account(account_id))["price_per_gb_toman"] == 1000
    await service.settle_usage(account_id, GIB)
    assert await service.get_accrued_target(account_id) == 1000
