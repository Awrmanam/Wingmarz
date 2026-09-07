import asyncio
from types import SimpleNamespace

import aiosqlite
import pytest

import config
import order_tracking
from database import Database
from operations_service import OperationsError
from order_queries import list_orders
from trial_experience_service import TrialExperienceService


async def seed(path, status='submitted'):
    db = Database(path)
    await db.init_db()
    async with aiosqlite.connect(path) as conn:
        await conn.execute('INSERT INTO orders(id,user_id,plan_id,status) VALUES(1,7,1,?)', (status,))
        await conn.commit()
    return db


def test_attention_disappears_after_success(tmp_path, monkeypatch):
    path = str(tmp_path / 'orders.db')
    monkeypatch.setattr(config, 'DATABASE_PATH', path)

    async def scenario():
        db = await seed(path)
        await order_tracking.observe_result(1)
        assert (await list_orders('failed'))[1] == 1
        await db.update_order(1, status='approved')
        await order_tracking.observe_result(1)
        assert not await order_tracking.needs_attention(1)
        assert (await list_orders('failed'))[1] == 0
    asyncio.run(scenario())


@pytest.mark.parametrize('status', ['approved', 'rejected', 'cancelled'])
def test_terminal_orders_cannot_be_rejected_or_reserved(tmp_path, status):
    path = str(tmp_path / 'orders.db')

    async def scenario():
        db = await seed(path, status)
        assert not await db.reject_pending_order(1, 42)
        assert await db.reserve_rebecca_provisioning(1, 'user', 'secret', None, 'lease', 1000, 300) is None
        with pytest.raises(OperationsError):
            await TrialExperienceService(path)._acquire_order_lock(1, 'user')
        assert (await db.get_order_by_id(1))['status'] == status
    asyncio.run(scenario())


def test_reject_and_issue_lease_are_mutually_exclusive(tmp_path):
    path = str(tmp_path / 'orders.db')

    async def scenario():
        db = await seed(path)
        service = TrialExperienceService(path)
        await service.ensure_schema()
        rejected, lease = await asyncio.gather(db.reject_pending_order(1, 42), service._acquire_order_lock(1, 'user'), return_exceptions=True)
        assert isinstance(rejected, bool)
        if rejected:
            assert isinstance(lease, OperationsError)
        else:
            assert isinstance(lease, tuple)
            assert not await db.reject_pending_order(1, 42)
    asyncio.run(scenario())


def test_rebecca_lease_blocks_rejection(tmp_path):
    path = str(tmp_path / 'orders.db')

    async def scenario():
        db = await seed(path)
        assert (await db.reserve_rebecca_provisioning(1, 'user', 'secret', None, 'lease', 1000, 300))['should_create']
        assert not await db.reject_pending_order(1, 42)
    asyncio.run(scenario())


def test_observer_preserves_exception_and_ignores_unauthorized(tmp_path, monkeypatch):
    path = str(tmp_path / 'orders.db')
    monkeypatch.setattr(config, 'DATABASE_PATH', path)
    monkeypatch.setattr(order_tracking, 'is_staff', lambda uid: uid == 42)

    @order_tracking.track_approval
    async def approve(callback):
        raise RuntimeError('provider error')

    async def scenario():
        await seed(path)
        for uid in [7, 42]:
            with pytest.raises(RuntimeError, match='provider error'):
                await approve(SimpleNamespace(data='order_approve_1', from_user=SimpleNamespace(id=uid)))
            assert await order_tracking.needs_attention(1) == (uid == 42)
    asyncio.run(scenario())
