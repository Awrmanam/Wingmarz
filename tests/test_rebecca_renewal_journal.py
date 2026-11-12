"""Production-contract renewal tests: no repeat remote mutations after restart/failure."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiosqlite
import pytest

import config
from database import Database
from models.schemas import AdminModel
from rebecca_api import RebeccaAPIError
import rebecca_renewal as renewal
from rebecca_renewal_journal import (
    RebeccaRenewalJournal, RenewalJournalError,
)


def run(coro):
    return asyncio.run(coro)


async def setup_db(path, *, reset=False, second_order=False):
    db = Database(str(path))
    await db.init_db()
    admin = AdminModel(
        user_id=42, admin_name="Reseller", marzban_username="seller-a",
        marzban_password="local-only", max_users=10,
        max_total_time=86400, max_total_traffic=100,
        validity_days=30,
    )
    assert await db.add_admin(admin)
    created = await db.get_admin_by_marzban_username("seller-a")
    assert created and created.id is not None
    async with aiosqlite.connect(path) as conn:
        await conn.execute(
            """INSERT INTO plans(id,name,price,traffic_limit_bytes,time_limit_seconds,max_users)
               VALUES (3,'New Plan',50000,200,172800,20)"""
        )
        await conn.execute(
            """INSERT INTO orders(
                id,user_id,plan_id,status,order_type,target_admin_id,
                apply_as_reset,delta_traffic_bytes,price_snapshot)
               VALUES (7,42,3,'submitted','renew',?,?,?,50000)""",
            (int(created.id), int(reset), 0 if reset else 50),
        )
        if second_order:
            await conn.execute(
                """INSERT INTO orders(
                    id,user_id,plan_id,status,order_type,target_admin_id,
                    apply_as_reset,delta_traffic_bytes,price_snapshot)
                   VALUES (8,42,3,'submitted','renew',?,?,50,50000)""",
                (int(created.id), 0),
            )
        await conn.commit()
    return db, int(created.id)


async def snapshot(path, admin_id):
    async with aiosqlite.connect(path) as conn:
        values = await (await conn.execute(
            "SELECT max_total_traffic,max_users FROM admins WHERE id=?", (admin_id,)
        )).fetchone()
        order = await (await conn.execute(
            "SELECT status FROM orders WHERE id=7"
        )).fetchone()
    return values, order[0]


def fixture(monkeypatch, tmp_path, *, reset=False, second_order=False):
    path = tmp_path / "bot.sqlite"
    db, admin_id = run(setup_db(path, reset=reset, second_order=second_order))
    journal = RebeccaRenewalJournal(str(path))
    monkeypatch.setattr(config, "DATABASE_PATH", str(path))
    monkeypatch.setattr(renewal, "db", db)
    monkeypatch.setattr(renewal, "rebecca_renewal_journal", journal)
    monkeypatch.setattr(renewal.group_ops_service, "audit", AsyncMock())
    remote = {
        "username": "seller-a", "status": "active",
        "data_limit": 100, "expire": 1_800_000_000, "users_limit": 10,
    }
    api = SimpleNamespace()
    api.find_admin = AsyncMock(side_effect=lambda _: dict(remote))
    async def update(username, payload):
        remote.update(payload)
        return dict(remote)
    api.update_admin_fields = AsyncMock(side_effect=update)
    api.reset_admin_usage = AsyncMock(return_value={})
    monkeypatch.setattr(renewal, "rebecca_api", api)
    order = {
        "id": 7, "user_id": 42, "plan_id": 3, "status": "submitted",
        "order_type": "renew", "target_admin_id": admin_id,
        "apply_as_reset": reset, "delta_traffic_bytes": 0 if reset else 50,
        "delta_time_seconds": 0, "delta_users": 0,
    }
    return path, db, admin_id, journal, api, remote, order


def test_incremental_applies_once_and_approves_atomically(tmp_path, monkeypatch):
    path, db, admin_id, journal, api, remote, order = fixture(monkeypatch, tmp_path)
    result = run(renewal.apply_rebecca_renewal(order, 99))
    assert result.admin_id == admin_id
    assert remote["data_limit"] == 150
    assert run(snapshot(path, admin_id)) == ((150, 10), "approved")
    api.update_admin_fields.assert_awaited_once()
    with pytest.raises(renewal.RebeccaRenewalError):
        run(renewal.apply_rebecca_renewal(order, 99))
    assert api.update_admin_fields.await_count == 1
    assert run(journal.list_needing_review()) == []


def test_ambiguous_remote_timeout_blocks_second_increment(tmp_path, monkeypatch):
    path, db, admin_id, journal, api, remote, order = fixture(monkeypatch, tmp_path)
    attempts = []
    async def timeout_after_apply(username, payload):
        remote.update(payload)
        attempts.append(dict(payload))
        raise RebeccaAPIError("upstream timed out")
    api.update_admin_fields.side_effect = timeout_after_apply
    for _ in range(2):
        with pytest.raises(renewal.RebeccaRenewalError, match="بررسی"):
            run(renewal.apply_rebecca_renewal(order, 99))
    assert len(attempts) == 1
    assert run(snapshot(path, admin_id)) == ((100, 10), "submitted")
    due = run(journal.list_needing_review())
    assert len(due) == 1 and due[0]["status"] == "needs_review"


def test_sqlite_failure_rolls_back_local_limits_and_blocks_retry(tmp_path, monkeypatch):
    path, db, admin_id, journal, api, remote, order = fixture(monkeypatch, tmp_path)
    async def fail_on_approval():
        async with aiosqlite.connect(path) as conn:
            await conn.executescript("""
                CREATE TRIGGER fail_renewal_approval
                BEFORE UPDATE ON orders
                WHEN NEW.status='approved'
                BEGIN SELECT RAISE(ABORT,'simulated write failure'); END;
            """)
    run(fail_on_approval())
    with pytest.raises(renewal.RebeccaRenewalError, match="بررسی"):
        run(renewal.apply_rebecca_renewal(order, 99))
    assert remote["data_limit"] == 150
    assert run(snapshot(path, admin_id)) == ((100, 10), "submitted")
    with pytest.raises(renewal.RebeccaRenewalError):
        run(renewal.apply_rebecca_renewal(order, 99))
    assert api.update_admin_fields.await_count == 1


def test_full_reset_timeout_never_issues_a_second_reset(tmp_path, monkeypatch):
    path, db, admin_id, journal, api, remote, order = fixture(monkeypatch, tmp_path, reset=True)
    async def reset_then_timeout(username):
        raise RebeccaAPIError("ambiguous reset acknowledgement")
    api.reset_admin_usage.side_effect = reset_then_timeout
    for _ in range(2):
        with pytest.raises(renewal.RebeccaRenewalError):
            run(renewal.apply_rebecca_renewal(order, 99))
    assert api.reset_admin_usage.await_count == 1
    assert api.update_admin_fields.await_count == 1
    assert run(snapshot(path, admin_id)) == ((100, 10), "submitted")


def test_two_orders_for_one_admin_cannot_execute_concurrently(tmp_path, monkeypatch):
    path, db, admin_id, journal, api, remote, order = fixture(
        monkeypatch, tmp_path, second_order=True
    )
    run(journal.reserve(7, admin_id, 99))
    with pytest.raises(RenewalJournalError):
        run(journal.reserve(8, admin_id, 99))
    assert run(journal.list_needing_review())[0]["order_id"] == 7


def test_renewal_rejects_order_for_another_panels_owner(tmp_path, monkeypatch):
    path, db, admin_id, journal, api, remote, order = fixture(monkeypatch, tmp_path)
    order["user_id"] = 777
    with pytest.raises(renewal.RebeccaRenewalError, match="متعلق"):
        run(renewal.apply_rebecca_renewal(order, 99))
    api.update_admin_fields.assert_not_awaited()
