"""Customer PAYG/FIXED classification and wallet reporting regressions."""
import sqlite3
from unittest.mock import AsyncMock

import pytest

import payg_customer_reporting as reports
from payg_offers_service import PaygOfferService
from payg_service import PaygService, GIB
from test_payg_historical_accrual import seed_account


@pytest.mark.asyncio
async def test_wallet_summary_uses_existing_ledger_and_never_mutates_balance(tmp_path,monkeypatch):
    service=PaygService(str(tmp_path/"wallet.db"))
    monkeypatch.setattr(reports,"payg_service",service)
    monkeypatch.setattr(reports,"payg_offer_service",PaygOfferService(service.db_path))
    await service.ensure_schema()
    account_id=seed_account(service.db_path,raw=GIB,cumulative=GIB,rate=1000,charged=1000)
    await service.credit_wallet(42,6000,kind="topup",reference_type="payg_topup",reference_id=1)
    await service.debit_wallet(42,1000,kind="payg_usage",reference_type="payg_account",reference_id=account_id)
    before=await service.get_balance(42)
    snapshot=await reports.wallet_summary(42)
    assert snapshot["balance"]==5000
    assert snapshot["topups"]==6000
    assert snapshot["usage"]==1000
    assert snapshot["debt"]==0
    assert snapshot["last_debit"]["amount_toman"]==-1000
    for category in ("all","topup","payg_usage","refund","adjustment","other"):
        await reports.ledger_page(42,category)
    assert await service.get_balance(42)==before
    with sqlite3.connect(service.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM wallet_ledger").fetchone()[0]==2


@pytest.mark.asyncio
async def test_ledger_pagination_filters_and_customer_isolation(tmp_path,monkeypatch):
    service=PaygService(str(tmp_path/"ledger.db"))
    monkeypatch.setattr(reports,"payg_service",service)
    monkeypatch.setattr(reports,"payg_offer_service",PaygOfferService(service.db_path))
    await service.ensure_schema()
    for n in range(11):
        await service.credit_wallet(42,100,kind="topup",reference_type="test",reference_id=n)
    await service.credit_wallet(43,999,kind="topup",reference_type="test",reference_id=99)
    first,more=await reports.ledger_page(42,"topup",page=0,size=8)
    second,last=await reports.ledger_page(42,"topup",page=1,size=8)
    assert len(first)==8 and more
    assert len(second)==3 and not last
    assert all(row["user_id"]==42 for row in first+second)
    assert len({row["id"] for row in first+second})==11
    with pytest.raises(ValueError):
        await reports.ledger_page(42,"unknown")


@pytest.mark.asyncio
async def test_payg_account_mapping_and_reports_are_owner_scoped(tmp_path,monkeypatch):
    service=PaygService(str(tmp_path/"account.db"))
    monkeypatch.setattr(reports,"payg_service",service)
    monkeypatch.setattr(reports,"payg_offer_service",PaygOfferService(service.db_path))
    await service.ensure_schema()
    account_id=seed_account(service.db_path,rate=2500)
    with sqlite3.connect(service.db_path) as conn:
        conn.execute(
            "INSERT INTO payg_account_services(account_id,provider,service_id)"
            "VALUES(?,'rebecca',7)",(account_id,)
        )
    account=await reports.account_for_customer(42,account_id)
    assert account["service_id"]==7
    assert await reports.account_for_customer(43,account_id) is None
    assert await reports.usage_report(43,account_id) is None
    result=await reports.usage_report(42,account_id)
    assert result["accrued"]==0
    assert result["audit"]==[]
    assert result["rates"]==[]


@pytest.mark.asyncio
async def test_legacy_payg_account_without_service_mapping_remains_visible(tmp_path,monkeypatch):
    service=PaygService(str(tmp_path/"legacy.db"))
    monkeypatch.setattr(reports,"payg_service",service)
    monkeypatch.setattr(reports,"payg_offer_service",PaygOfferService(service.db_path))
    await service.ensure_schema()
    account_id=seed_account(service.db_path,rate=1000)
    account=await reports.account_for_customer(42,account_id)
    assert account is not None
    assert account["service_id"] is None
