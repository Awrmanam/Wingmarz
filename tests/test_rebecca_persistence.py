import asyncio

import aiosqlite

from database import Database
from models.schemas import AdminModel


def run(coro):
    return asyncio.run(coro)


async def _prepared_db(path):
    db = Database(str(path))
    await db.init_db()
    async with aiosqlite.connect(path) as conn:
        await conn.execute("INSERT INTO plans (id, name) VALUES (1, 'Plan')")
        await conn.execute("INSERT INTO orders (id, user_id, plan_id) VALUES (1, 42, 1)")
        await conn.commit()
    return db


def _admin(username):
    return AdminModel(user_id=42, admin_name="User 42", marzban_username=username,
                      marzban_password="reserved-password", max_users=7,
                      max_total_time=86400, max_total_traffic=123, validity_days=1)


def test_finalize_is_atomic_and_clears_temporary_password(tmp_path):
    async def scenario():
        path = tmp_path / "db.sqlite"
        db = await _prepared_db(path)
        reservation = await db.reserve_rebecca_provisioning(1, "reserved_1234", "reserved-password", 123456, "lease", 1000, 300)
        assert reservation["should_create"]
        assert await db.finalize_rebecca_provisioning(1, _admin("reserved_1234"), 99, "lease")
        async with aiosqlite.connect(path) as conn:
            row = await (await conn.execute(
                "SELECT status, rebecca_password, rebecca_provision_state FROM orders WHERE id=1"
            )).fetchone()
            count = (await (await conn.execute("SELECT COUNT(*) FROM admins")).fetchone())[0]
        assert row == ("approved", None, "completed") and count == 1
    run(scenario())


def test_finalize_rolls_back_admin_when_reserved_username_does_not_match(tmp_path):
    async def scenario():
        path = tmp_path / "db.sqlite"
        db = await _prepared_db(path)
        await db.reserve_rebecca_provisioning(1, "reserved_1234", "reserved-password", None, "lease", 1000, 300)
        assert not await db.finalize_rebecca_provisioning(1, _admin("different_9999"), 99, "lease")
        async with aiosqlite.connect(path) as conn:
            status = (await (await conn.execute("SELECT status FROM orders WHERE id=1")).fetchone())[0]
            count = (await (await conn.execute("SELECT COUNT(*) FROM admins")).fetchone())[0]
        assert status == "pending" and count == 0
    run(scenario())


def test_active_lease_cannot_be_stolen_but_stale_lease_can_recover(tmp_path):
    async def scenario():
        path = tmp_path / "db.sqlite"
        db = await _prepared_db(path)
        first = await db.reserve_rebecca_provisioning(
            1, "reserved_1234", "reserved-password", 123456, "lease-a", 1000, 300
        )
        active = await db.reserve_rebecca_provisioning(
            1, "ignored", "ignored", 999, "lease-b", 1100, 300
        )
        stale = await db.reserve_rebecca_provisioning(
            1, "ignored", "ignored", 999, "lease-c", 1401, 300
        )
        assert first["should_create"] and not first["recovery"]
        assert active["in_progress"] and not active["should_create"]
        assert stale["should_create"] and stale["recovery"]
        assert stale["rebecca_username"] == "reserved_1234"
        assert stale["rebecca_password"] == "reserved-password"
        assert stale["rebecca_lease_token"] == "lease-c"
    run(scenario())


def test_stale_worker_is_fenced_from_all_reservation_mutations(tmp_path):
    async def scenario():
        path = tmp_path / "db.sqlite"
        db = await _prepared_db(path)
        await db.reserve_rebecca_provisioning(
            1, "reserved_1234", "reserved-password", 123456, "lease-a", 1000, 300
        )
        claimed = await db.reserve_rebecca_provisioning(
            1, "ignored", "ignored", 999, "lease-b", 1401, 300
        )
        assert claimed["rebecca_lease_token"] == "lease-b"

        assert not await db.update_rebecca_reserved_username(
            1, "reserved_1234", "stale_9999", "lease-a"
        )
        assert not await db.clear_rebecca_reservation(1, "reserved_1234", "lease-a")
        assert not await db.finalize_rebecca_provisioning(
            1, _admin("reserved_1234"), 99, "lease-a"
        )

        assert await db.update_rebecca_reserved_username(
            1, "reserved_1234", "current_5678", "lease-b"
        )
        assert await db.finalize_rebecca_provisioning(
            1, _admin("current_5678"), 99, "lease-b"
        )
    run(scenario())


def test_current_worker_can_clear_but_stale_worker_cannot(tmp_path):
    async def scenario():
        path = tmp_path / "db.sqlite"
        db = await _prepared_db(path)
        await db.reserve_rebecca_provisioning(
            1, "reserved_1234", "reserved-password", None, "lease-a", 1000, 300
        )
        await db.reserve_rebecca_provisioning(
            1, "ignored", "ignored", None, "lease-b", 1401, 300
        )
        assert not await db.clear_rebecca_reservation(1, "reserved_1234", "lease-a")
        assert await db.clear_rebecca_reservation(1, "reserved_1234", "lease-b")
    run(scenario())
