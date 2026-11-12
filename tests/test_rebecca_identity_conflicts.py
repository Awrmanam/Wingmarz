"""Durable ownership disagreement queue; remote Telegram changes do not move panels."""
import asyncio
import aiosqlite
from rebecca_identity_conflicts import RebeccaIdentityConflicts


def run(coro):
    return asyncio.run(coro)


def test_conflicts_persist_and_deduplicate_across_sync_runs(tmp_path):
    path = str(tmp_path / "wingmarz.db")
    manager = RebeccaIdentityConflicts(path)
    async def scenario():
        await manager.report("seller-a", 111, 555)
        await manager.report("seller-a", 111, 555)
        row = (await manager.list_open())[0]
        assert row["username"] == "seller-a"
        assert row["local_telegram_id"] == 111
        assert row["remote_telegram_id"] == 555
        assert row["occurrences"] == 2
        async with aiosqlite.connect(path) as db:
            count = (await (await db.execute(
                "SELECT COUNT(*) FROM rebecca_identity_conflicts"
            )).fetchone())[0]
        assert count == 1
        await manager.clear_when_matching("seller-a", 111)
        assert await manager.list_open() == []
    run(scenario())


def test_conflict_details_refresh_but_never_change_existing_local_user_id(tmp_path):
    path = str(tmp_path / "wingmarz.db")
    manager = RebeccaIdentityConflicts(path)
    async def scenario():
        await manager.report("seller-a", 111, 555)
        await manager.report("seller-a", 111, 777)
        row = (await manager.list_open())[0]
        assert row["local_telegram_id"] == 111
        assert row["remote_telegram_id"] == 777
        await manager.clear_when_matching("seller-a", 111)
        assert await manager.list_open() == []
    run(scenario())
