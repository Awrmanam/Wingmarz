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
