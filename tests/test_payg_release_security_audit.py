"""Regression coverage for operator auth and historical-rate financial hold."""
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import payg_service as mod
from payg_service import GIB, PaygError, PaygService
from test_payg_historical_accrual import seed_account


@pytest.mark.asyncio
async def test_topup_approval_and_rejection_require_backend_operator(tmp_path,monkeypatch):
    service = PaygService(str(tmp_path / "auth.db"))
    await service.ensure_schema()
    monkeypatch.setattr(mod.config,"SUDO_ADMINS",[99])
    topup_id = await service.create_topup(42,1000)
    with pytest.raises(PaygError,match="operator permission"):
        await service.approve_topup(topup_id,42)
    with pytest.raises(PaygError,match="operator permission"):
        await service.reject_topup(topup_id,42)
    with pytest.raises(PaygError,match="operator permission"):
        await service.hold_topup_for_kyc(topup_id,42)
    assert (await service.get_topup(topup_id))["status"] == "pending"
    assert await service.get_balance(42) == 0


@pytest.mark.asyncio
async def test_hold_release_uses_historical_accrual_not_current_rate(tmp_path,monkeypatch):
    path = str(tmp_path / "historical-hold.db")
    service = PaygService(path)
    await service.ensure_schema()
    account_id = seed_account(path,raw=GIB,cumulative=GIB,rate=2000,charged=1000)
    await service.credit_wallet(42,100,kind="test_seed")
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE payg_accounts SET status='suspended' WHERE id=?",(account_id,))
        conn.execute(
            "INSERT INTO payg_billing_state "
            "(account_id,accrued_toman,remainder_numerator,last_priced_bytes) "
            "VALUES(?,1000,0,?)",(account_id,GIB),
        )
    monkeypatch.setattr(mod.config,"SUDO_ADMINS",[99])
    monkeypatch.setattr(mod.db,"get_admin_by_id",AsyncMock(
        return_value=SimpleNamespace(marzban_username="payg42_7",is_active=True,
                                     deactivated_reason="")
    ))
    monkeypatch.setattr(mod.rebecca_api,"find_admin",AsyncMock(
        return_value={"status":"active"}
    ))
    with pytest.raises(PaygError,match="operator permission"):
        await service.release_financial_hold(account_id,42)
    assert await service.release_financial_hold(account_id,99)
    assert (await service.get_account(account_id))["status"] == "active"
    assert await service.get_balance(42) == 100


@pytest.mark.asyncio
async def test_hold_release_refuses_unresolved_discount_boundary(tmp_path,monkeypatch):
    path = str(tmp_path / "disputed-hold.db")
    service = PaygService(path)
    await service.ensure_schema()
    account_id = seed_account(path,rate=1000)
    await service.credit_wallet(42,100,kind="test_seed")
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE payg_accounts SET status='suspended' WHERE id=?",(account_id,))
        conn.execute(
            "INSERT INTO payg_billing_state "
            "(account_id,accrued_toman,remainder_numerator,last_priced_bytes) "
            "VALUES(?,0,0,0)",(account_id,),
        )
        conn.execute(
            "INSERT INTO payg_discount_targets(account_id,base_rate,last_error) "
            "VALUES(?,1000,'unattributable outage')",(account_id,),
        )
    monkeypatch.setattr(mod.config,"SUDO_ADMINS",[99])
    monkeypatch.setattr(mod.db,"get_admin_by_id",AsyncMock(
        return_value=SimpleNamespace(marzban_username="payg42_7",is_active=True,
                                     deactivated_reason="")
    ))
    monkeypatch.setattr(mod.rebecca_api,"find_admin",AsyncMock(
        return_value={"status":"active"}
    ))
    with pytest.raises(PaygError,match="disputed discount boundary"):
        await service.release_financial_hold(account_id,99)
    assert (await service.get_account(account_id))["status"] == "suspended"


@pytest.mark.asyncio
async def test_missing_historical_accrual_never_reprices_changed_rate(tmp_path):
    path=str(tmp_path/"changed-rate.db")
    service=PaygService(path)
    await service.ensure_schema()
    account_id=seed_account(path,raw=GIB,cumulative=GIB,rate=2000,charged=1000)
    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT INTO payg_rate_history"
            "(account_id,actor_id,previous_rate,new_rate,boundary_raw_bytes,boundary_cumulative_bytes)"
            "VALUES(?,99,1000,2000,0,0)",(account_id,),
        )
    with pytest.raises(PaygError,match="historical accrual missing"):
        await service.get_accrued_target(account_id)
