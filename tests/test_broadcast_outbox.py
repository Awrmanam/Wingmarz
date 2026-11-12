"""Broadcast outbox correctness with isolated SQLite and fake Telegram client."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiosqlite
import pytest

import config
from broadcast_outbox import BroadcastError, BroadcastOutbox


def _seed(service, *, token="event-123", users=None, now=1000):
    return service.create_job(
        actor_id=9001,request_key=token,audience="all",
        text="This is a test, not a live notification.",
        recipient_ids=[101,102,103] if users is None else users,
        now=now,
    )


def test_enqueue_is_idempotent_and_isolated_from_financial_tables(tmp_path,monkeypatch):
    monkeypatch.setattr(config,"SUDO_ADMINS",[9001])
    outbox = BroadcastOutbox(str(tmp_path / "queue.sqlite"))

    async def scenario():
        job_id = await _seed(outbox)
        assert job_id == await _seed(outbox)
        job = await outbox.get_job(job_id)
        assert job["total"] == 3 and job["queued"] == 3
        assert job["status"] == "running"
        async with aiosqlite.connect(outbox.db_path) as conn:
            async with conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ) as cur:
                names = {row[0] for row in await cur.fetchall()}
        assert "wallets" not in names
        assert "payg_accounts" not in names
        with pytest.raises(BroadcastError,match="permission"):
            await outbox.create_job(
                actor_id=5,request_key="other-key",audience="all",
                text="hello",recipient_ids=[200],
            )

    asyncio.run(scenario())


def test_parallel_claims_have_disjoint_receipts(tmp_path,monkeypatch):
    monkeypatch.setattr(config,"SUDO_ADMINS",[9001])
    outbox = BroadcastOutbox(str(tmp_path / "race.sqlite"))

    async def scenario():
        await _seed(outbox)
        (token1,a), (token2,b) = await asyncio.gather(
            outbox.claim_due(now=1000,limit=2),
            outbox.claim_due(now=1000,limit=2),
        )
        left = {r["recipient_telegram_id"] for r in a}
        right = {r["recipient_telegram_id"] for r in b}
        assert len(left|right) == 3
        assert not left & right
        assert token1 != token2

    asyncio.run(scenario())


def test_successful_receipts_survive_restart_without_duplication(tmp_path,monkeypatch):
    monkeypatch.setattr(config,"SUDO_ADMINS",[9001])
    path = str(tmp_path / "restart.sqlite")
    outbox = BroadcastOutbox(path)
    bot = SimpleNamespace(send_message=AsyncMock())

    async def scenario():
        job_id = await _seed(outbox,users=[101,102])
        assert await outbox.process(bot,now=1000,limit=1) == 1
        replacement = BroadcastOutbox(path)
        assert await replacement.process(bot,now=1001,limit=2) == 1
        assert await replacement.process(bot,now=1002,limit=2) == 0
        job = await replacement.get_job(job_id)
        assert job["total"] == job["sent"] == 2
        assert job["queued"] == 0
        assert job["status"] == "completed"
        assert bot.send_message.await_count == 2
        assert all(call.kwargs["parse_mode"] is None
                   for call in bot.send_message.await_args_list)

    asyncio.run(scenario())


def test_pause_resume_and_failures_are_retried_with_backoff(tmp_path,monkeypatch):
    monkeypatch.setattr(config,"SUDO_ADMINS",[9001])
    outbox = BroadcastOutbox(str(tmp_path / "pause.sqlite"))
    bot = SimpleNamespace(send_message=AsyncMock(side_effect=TimeoutError("offline")))

    async def scenario():
        job_id = await _seed(outbox,users=[101])
        assert await outbox.set_paused(job_id,9001,True)
        assert await outbox.process(bot,now=1000) == 0
        bot.send_message.assert_not_awaited()
        assert await outbox.set_paused(job_id,9001,False)
        assert await outbox.process(bot,now=1000) == 0
        assert bot.send_message.await_count == 1
        assert (await outbox.get_job(job_id))["queued"] == 1
        bot.send_message.side_effect = None
        assert await outbox.process(bot,now=1029) == 0
        assert await outbox.process(bot,now=1030) == 1
        assert bot.send_message.await_count == 2
        assert (await outbox.get_job(job_id))["status"] == "completed"

    asyncio.run(scenario())


def test_expired_claim_is_released_after_crash_and_old_ack_is_ignored(tmp_path,monkeypatch):
    monkeypatch.setattr(config,"SUDO_ADMINS",[9001])
    path = str(tmp_path / "lease.sqlite")
    original = BroadcastOutbox(path)

    async def scenario():
        job_id = await _seed(original,users=[101])
        old_token,first = await original.claim_due(now=1000)
        assert len(first) == 1
        newer = BroadcastOutbox(path)
        token,rows = await newer.claim_due(now=1181)
        assert len(rows) == 1 and token != old_token
        await original.finish(job_id,101,old_token,delivered=True,now=1182)
        assert (await newer.get_job(job_id))["queued"] == 1
        await newer.finish(job_id,101,token,delivered=True,now=1183)
        assert (await newer.get_job(job_id))["status"] == "completed"

    asyncio.run(scenario())
