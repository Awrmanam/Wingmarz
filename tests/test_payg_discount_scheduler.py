"""Scheduled PAYG discount activation and expiry, including failure recovery."""
from unittest.mock import AsyncMock
import pytest

from payg_service import PaygError, PaygService
from test_payg_historical_accrual import seed_account


@pytest.mark.asyncio
async def test_scheduled_activation_expiry_and_retry(tmp_path, monkeypatch):
    service = PaygService(str(tmp_path / "discount.db"))
    await service.ensure_schema()
    account_id = seed_account(service.db_path, rate=1000)
    monkeypatch.setenv("PAYG_OWNER_ID", "99")
    await service.configure_customer_discount(
        account_id,99,mode="percent",value=20,
        base_rate=1000,starts_at=100,expires_at=200,
    )
    calls = []

    async def fake_switch(account,actor,rate):
        calls.append(rate)
        # Only a successful remote transition may update the billing rate.
        import aiosqlite
        async with aiosqlite.connect(service.db_path) as conn:
            await conn.execute(
                "UPDATE payg_accounts SET price_per_gb_toman=? WHERE id=?",
                (rate,account),
            )
            await conn.commit()

    monkeypatch.setattr(service,"switch_rate_after_rebecca_sync",fake_switch)
    assert (await service.reconcile_customer_discounts(now=99))[account_id] == "current"
    assert (await service.reconcile_customer_discounts(now=100))[account_id] == "applied"
    assert calls == [800]
    assert (await service.reconcile_customer_discounts(now=150))[account_id] == "current"
    assert (await service.reconcile_customer_discounts(now=200))[account_id] == "applied"
    assert calls == [800,1000]


@pytest.mark.asyncio
async def test_failed_transition_stays_pending_and_blocks_billing(tmp_path, monkeypatch):
    service = PaygService(str(tmp_path / "discount.db"))
    await service.ensure_schema()
    account_id = seed_account(service.db_path, rate=1000)
    monkeypatch.setenv("PAYG_OWNER_ID","99")
    await service.configure_customer_discount(
        account_id,99,mode="fixed",value=500,
        base_rate=1000,starts_at=100,
    )
    monkeypatch.setattr(
        service,"switch_rate_after_rebecca_sync",
        AsyncMock(side_effect=TimeoutError("Rebecca unavailable")),
    )
    assert (await service.reconcile_customer_discounts(now=101))[account_id] == "pending"
    assert (await service.get_account(account_id))["price_per_gb_toman"] == 1000
    with pytest.raises(PaygError,match="pending discount boundary"):
        await service.settle_usage(account_id,1024**3)
    restarted = PaygService(service.db_path)
    assert (await restarted.reconcile_customer_discounts(now=101))[account_id] == "manual_review"


@pytest.mark.asyncio
async def test_cancel_queues_public_rate_transition(tmp_path, monkeypatch):
    service = PaygService(str(tmp_path / "discount.db"))
    await service.ensure_schema()
    account_id = seed_account(service.db_path, rate=800)
    monkeypatch.setenv("PAYG_OWNER_ID","99")
    await service.configure_customer_discount(
        account_id,99,mode="percent",value=20,
        base_rate=1000,starts_at=100,
    )
    assert await service.cancel_customer_discount(account_id,99)
    called = []

    async def switch(account,actor,rate):
        called.append(rate)
        import aiosqlite
        async with aiosqlite.connect(service.db_path) as conn:
            await conn.execute(
                "UPDATE payg_accounts SET price_per_gb_toman=? WHERE id=?",
                (rate,account),
            )
            await conn.commit()

    monkeypatch.setattr(service,"switch_rate_after_rebecca_sync",switch)
    assert (await service.reconcile_customer_discounts(now=110))[account_id] == "applied"
    assert called == [1000]
