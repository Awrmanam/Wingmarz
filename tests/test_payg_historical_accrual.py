"""PAYG historical accrual: legacy migration, rate boundaries and integer billing."""
import sqlite3

import pytest

from payg_service import GIB, PaygError, PaygService


def seed_account(path, *, raw=0, cumulative=0, rate=1000, charged=0):
    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT INTO payg_accounts "
            "(user_id,admin_id,provider,status,price_per_gb_toman,"
            "last_raw_usage_bytes,cumulative_usage_bytes,charged_toman_total) "
            "VALUES(42,420,'rebecca','active',?,?,?,?)",
            (rate, raw, cumulative, charged),
        )
        return conn.execute("SELECT id FROM payg_accounts").fetchone()[0]


@pytest.mark.asyncio
async def test_rate_change_preserves_historical_debt(tmp_path, monkeypatch):
    path = str(tmp_path / "billing.db")
    service = PaygService(path)
    await service.ensure_schema()
    account_id = seed_account(path, raw=GIB, cumulative=GIB)
    monkeypatch.setenv("PAYG_OWNER_ID", "99")
    await service.change_account_rate(account_id, 99, 2000, verified_raw_usage_bytes=GIB)
    assert await service.get_accrued_target(account_id) == 1000
    result = await service.settle_usage(account_id, 2 * GIB)
    assert result.cumulative_usage_bytes == 2 * GIB
    assert await service.get_accrued_target(account_id) == 3000
    assert result.outstanding_toman == 3000
    await service.change_account_rate(account_id, 99, 500, verified_raw_usage_bytes=2 * GIB)
    await service.settle_usage(account_id, 3 * GIB)
    assert await service.get_accrued_target(account_id) == 3500
    with sqlite3.connect(path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM payg_rate_history WHERE account_id=?", (account_id,)
        ).fetchone()[0] == 2


@pytest.mark.asyncio
async def test_rate_change_requires_owner_and_exact_boundary(tmp_path, monkeypatch):
    path = str(tmp_path / "billing.db")
    service = PaygService(path)
    await service.ensure_schema()
    account_id = seed_account(path, raw=GIB, cumulative=GIB)
    monkeypatch.setenv("PAYG_OWNER_ID", "99")
    with pytest.raises(PaygError, match="owner"):
        await service.change_account_rate(account_id, 100, 500, verified_raw_usage_bytes=GIB)
    with pytest.raises(PaygError, match="unsettled"):
        await service.change_account_rate(account_id, 99, 500, verified_raw_usage_bytes=2 * GIB)
    assert (await service.get_account(account_id))["price_per_gb_toman"] == 1000


@pytest.mark.asyncio
async def test_fractional_bytes_remain_exact_across_rate_switch(tmp_path, monkeypatch):
    path = str(tmp_path / "billing.db")
    service = PaygService(path)
    await service.ensure_schema()
    account_id = seed_account(path)
    monkeypatch.setenv("PAYG_OWNER_ID", "99")
    await service.settle_usage(account_id, GIB // 2)
    assert await service.get_accrued_target(account_id) == 500
    await service.change_account_rate(account_id, 99, 2000, verified_raw_usage_bytes=GIB // 2)
    await service.settle_usage(account_id, GIB)
    assert await service.get_accrued_target(account_id) == 1500


@pytest.mark.asyncio
async def test_old_database_migration_is_additive(tmp_path):
    path = str(tmp_path / "legacy.db")
    service = PaygService(path)
    await service.ensure_schema()
    account_id = seed_account(path, raw=GIB, cumulative=GIB, rate=900)
    await service.ensure_schema()
    assert await service.get_accrued_target(account_id) == 900
    await service.settle_usage(account_id, GIB)
    assert await service.get_accrued_target(account_id) == 900
