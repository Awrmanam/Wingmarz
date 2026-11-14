"""Owner-only discount policies use exact integer arithmetic."""
import sqlite3
import pytest
from payg_service import PaygError, PaygService
from test_payg_historical_accrual import seed_account


def test_discount_precedence_and_validation():
    assert PaygService.discount_rate(999, "percent", 25) == 749
    assert PaygService.discount_rate(999, "fixed", 300) == 300
    assert PaygService.discount_rate(999, "percent", 0) == 999
    with pytest.raises(PaygError):
        PaygService.discount_rate(999, "percent", 100)


@pytest.mark.asyncio
async def test_discount_persistence_audit_and_owner(tmp_path, monkeypatch):
    path = str(tmp_path / "discount.db")
    service = PaygService(path)
    await service.ensure_schema()
    account_id = seed_account(path)
    monkeypatch.setenv("PAYG_OWNER_ID", "99")
    with pytest.raises(PaygError, match="owner"):
        await service.configure_customer_discount(
            account_id, 100, mode="percent", value=20,
            base_rate=1000, starts_at=100,
        )
    await service.configure_customer_discount(
        account_id, 99, mode="percent", value=20,
        base_rate=1000, starts_at=100, expires_at=200,
    )
    await service.configure_customer_discount(
        account_id, 99, mode="fixed", value=500,
        base_rate=1000, starts_at=100, expires_at=300,
    )
    record = await service.get_customer_discount(account_id)
    assert (record["mode"], record["value"], record["expires_at"]) == ("fixed", 500, 300)
    with sqlite3.connect(path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM payg_customer_discount_audit "
            "WHERE account_id=? AND actor_id=99", (account_id,),
        ).fetchone()[0] == 2


@pytest.mark.asyncio
async def test_discount_cancel_requires_owner_and_audits(tmp_path, monkeypatch):
    path = str(tmp_path / "discount.db")
    service = PaygService(path)
    await service.ensure_schema()
    account_id = seed_account(path)
    monkeypatch.setenv("PAYG_OWNER_ID", "99")
    await service.configure_customer_discount(
        account_id, 99, mode="percent", value=20,
        base_rate=1000, starts_at=100,
    )
    with pytest.raises(PaygError, match="owner"):
        await service.cancel_customer_discount(account_id, 42)
    assert await service.cancel_customer_discount(account_id, 99)
    assert not await service.cancel_customer_discount(account_id, 99)
    assert await service.get_customer_discount(account_id) is None
    with sqlite3.connect(path) as conn:
        assert conn.execute(
            "SELECT action FROM payg_customer_discount_audit "
            "WHERE account_id=? ORDER BY id DESC LIMIT 1", (account_id,),
        ).fetchone()[0] == "cancel"
