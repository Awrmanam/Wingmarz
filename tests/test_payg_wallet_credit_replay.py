"""Financial credit replay regressions (isolated SQLite database)."""
import pytest
from payg_service import PaygError, PaygService


@pytest.mark.asyncio
async def test_migration_refund_replay_is_idempotent(tmp_path):
    service = PaygService(str(tmp_path / "finance.db"))
    first = await service.credit_wallet(
        42, 1200, kind="migration_refund",
        reference_type="payg_migration", reference_id=7,
    )
    second = await service.credit_wallet(
        42, 1200, kind="migration_refund",
        reference_type="payg_migration", reference_id=7,
    )
    assert (first, second) == (1200, 1200)
    assert len(await service.wallet_history(42)) == 1


@pytest.mark.asyncio
async def test_conflicting_refund_replay_is_rejected(tmp_path):
    service = PaygService(str(tmp_path / "finance.db"))
    await service.credit_wallet(
        42, 1200, kind="migration_refund",
        reference_type="payg_migration", reference_id=7,
    )
    with pytest.raises(PaygError):
        await service.credit_wallet(
            42, 2400, kind="migration_refund",
            reference_type="payg_migration", reference_id=7,
        )
    assert await service.get_balance(42) == 1200


@pytest.mark.asyncio
async def test_concurrent_refund_replay_posts_once(tmp_path):
    import asyncio
    service = PaygService(str(tmp_path / "finance.db"))
    values = await asyncio.gather(*(
        service.credit_wallet(
            42, 1200, kind="migration_refund",
            reference_type="payg_migration", reference_id=7,
        ) for _ in range(3)
    ))
    assert values == [1200] * 3
    assert await service.get_balance(42) == 1200
    assert len(await service.wallet_history(42)) == 1


@pytest.mark.asyncio
async def test_legacy_ledger_schema_is_preserved(tmp_path):
    import sqlite3
    path = str(tmp_path / "legacy.db")
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE wallet_ledger ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, "
            "amount_toman INTEGER NOT NULL, balance_after_toman INTEGER NOT NULL, "
            "kind TEXT NOT NULL, reference_type TEXT, reference_id INTEGER, "
            "note TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        )
        # Older deployments may already contain duplicate refund rows.
        conn.executemany(
            "INSERT INTO wallet_ledger "
            "(user_id,amount_toman,balance_after_toman,kind,reference_type,reference_id) "
            "VALUES(?,?,?,?,?,?)",
            [(42, 1200, 1200, "migration_refund", "payg_migration", 7),
             (42, 1200, 2400, "migration_refund", "payg_migration", 7)],
        )
    service = PaygService(path)
    await service.ensure_schema()
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM wallet_ledger").fetchone()[0] == 2
