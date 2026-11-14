"""Disputed discount interval is never charged without a defensible boundary."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
import sqlite3

import pytest

import payg_service as module
from payg_service import PaygError, PaygService, GIB
from test_payg_historical_accrual import seed_account


@pytest.mark.asyncio
async def test_owner_audited_writeoff_preserves_old_debt(tmp_path,monkeypatch):
    path = str(tmp_path / "writeoff.db")
    service = PaygService(path)
    await service.ensure_schema()
    account_id = seed_account(path,rate=1000)
    monkeypatch.setenv("PAYG_OWNER_ID","99")
    await service.configure_customer_discount(
        account_id,99,mode="fixed",value=500,
        base_rate=1000,starts_at=100,
    )
    with sqlite3.connect(path) as conn:
        conn.execute(
            "UPDATE payg_discount_targets SET last_error='Rebecca outage' WHERE account_id=?",
            (account_id,),
        )
    monkeypatch.setattr(module.db,"get_admin_by_id",AsyncMock(
        return_value=SimpleNamespace(marzban_username="payg42")
    ))
    monkeypatch.setattr(module.rebecca_api,"get_admin_usage",AsyncMock(return_value=GIB))
    with pytest.raises(PaygError,match="owner"):
        await service.resolve_discount_boundary_without_charge(
            account_id,100,reason="Outage interval is not attributable"
        )
    await service.resolve_discount_boundary_without_charge(
        account_id,99,reason="Outage interval is not attributable"
    )
    account = await service.get_account(account_id)
    assert account["last_raw_usage_bytes"] == GIB
    assert account["cumulative_usage_bytes"] == 0
    assert await service.get_accrued_target(account_id) == 0
    with sqlite3.connect(path) as conn:
        assert conn.execute(
            "SELECT event_type FROM payg_usage_audit WHERE account_id=? "
            "ORDER BY id DESC LIMIT 1",(account_id,),
        ).fetchone()[0] == "discount_boundary_writeoff"
    await service.settle_usage(account_id,2*GIB)
    assert await service.get_accrued_target(account_id) == 500


@pytest.mark.asyncio
async def test_discount_edit_cannot_clear_existing_hold(tmp_path,monkeypatch):
    path = str(tmp_path / "hold.db")
    service = PaygService(path)
    await service.ensure_schema()
    account_id = seed_account(path,rate=1000)
    monkeypatch.setenv("PAYG_OWNER_ID","99")
    await service.configure_customer_discount(
        account_id,99,mode="percent",value=20,base_rate=1000,starts_at=100,
    )
    with sqlite3.connect(path) as conn:
        conn.execute(
            "UPDATE payg_discount_targets SET last_error='unknown interval' WHERE account_id=?",
            (account_id,),
        )
    await service.configure_customer_discount(
        account_id,99,mode="percent",value=25,base_rate=1000,starts_at=100,
    )
    with pytest.raises(PaygError,match="pending discount boundary"):
        await service.settle_usage(account_id,GIB)
