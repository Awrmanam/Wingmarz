"""Financial safety tests with an isolated SQLite database and fake Rebecca RPCs."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiosqlite
import pytest

import payg_service as module
from payg_service import GIB, PaygError, PaygService


async def _account(service, *, status="active", used=0, cumulative=0, charged=0):
    await service.ensure_schema()
    async with aiosqlite.connect(service.db_path) as conn:
        cur = await conn.execute(
            "INSERT INTO payg_accounts("
            "user_id,admin_id,provider,status,price_per_gb_toman,"
            "last_raw_usage_bytes,cumulative_usage_bytes,charged_toman_total)"
            " VALUES(8,88,'rebecca',?,10000,?,?,?)",
            (status,used,cumulative,charged),
        )
        await conn.commit()
        return int(cur.lastrowid)


def _operator(monkeypatch, raw):
    monkeypatch.setattr(module.config, "SUDO_ADMINS", [9001])
    monkeypatch.setattr(
        module.db, "get_admin_by_id",
        AsyncMock(return_value=SimpleNamespace(marzban_username="reseller-88")),
    )
    monkeypatch.setattr(
        module.rebecca_api, "get_admin_usage", AsyncMock(return_value=raw),
    )
    monkeypatch.setattr(
        module.rebecca_api, "find_admin",
        AsyncMock(return_value={"status": "active"}),
    )


def test_verified_reset_starts_new_epoch_without_charging_ambiguous_bytes(tmp_path, monkeypatch):
    svc = PaygService(str(tmp_path / "usage.db"))
    _operator(monkeypatch, GIB // 2)

    async def scenario():
        account_id = await _account(
            svc, used=2*GIB, cumulative=2*GIB, charged=20_000,
        )
        await svc.credit_wallet(8, 100_000, kind="seed")
        with pytest.raises(PaygError, match="manual reconciliation"):
            await svc.settle_usage(account_id, GIB // 2)
        review = (await svc.list_counter_reviews())[0]
        with pytest.raises(PaygError, match="operator permission"):
            await svc.resolve_counter_review(review["id"], 123, approve_reset=True)
        result = await svc.resolve_counter_review(
            review["id"], 9001, approve_reset=True,
        )
        assert result["status"] == "approved"
        assert result["baseline_raw_bytes"] == GIB // 2
        assert not await svc.list_counter_reviews()
        assert (await svc.settle_usage(account_id, GIB // 2)).charged_now_toman == 0
        later = await svc.settle_usage(account_id, 3 * GIB // 4)
        assert later.charged_now_toman == 2500
        assert later.cumulative_usage_bytes == 2 * GIB + GIB // 4
        assert await svc.get_balance(8) == 97_500
        with pytest.raises(PaygError, match="no longer pending"):
            await svc.resolve_counter_review(review["id"], 9001, approve_reset=True)
        async with aiosqlite.connect(svc.db_path) as conn:
            async with conn.execute(
                "SELECT epoch,baseline_raw_bytes FROM payg_usage_epochs "
                "WHERE account_id=? ORDER BY epoch", (account_id,),
            ) as cur:
                epochs = await cur.fetchall()
            async with conn.execute(
                "SELECT event_type FROM payg_usage_audit WHERE account_id=?",
                (account_id,),
            ) as cur:
                events = {row[0] for row in await cur.fetchall()}
        assert [row[0] for row in epochs] == [0, 1]
        assert epochs[1][1] == GIB // 2
        assert {"counter_regression","counter_approved","settled"} <= events

    asyncio.run(scenario())


def test_stale_api_counter_can_be_dismissed_only_after_recovery(tmp_path, monkeypatch):
    svc = PaygService(str(tmp_path / "recovery.db"))
    _operator(monkeypatch, GIB // 2)

    async def scenario():
        account_id = await _account(svc, used=GIB, cumulative=GIB, charged=10_000)
        await svc.credit_wallet(8, 50_000, kind="seed")
        with pytest.raises(PaygError):
            await svc.settle_usage(account_id, GIB // 2)
        review_id = (await svc.list_counter_reviews())[0]["id"]
        with pytest.raises(PaygError, match="still regressed"):
            await svc.resolve_counter_review(review_id, 9001, approve_reset=False)
        module.rebecca_api.get_admin_usage.return_value = 2*GIB
        decision = await svc.resolve_counter_review(review_id, 9001, approve_reset=False)
        assert decision["status"] == "dismissed"
        result = await svc.settle_usage(account_id, 2 * GIB)
        assert result.charged_now_toman == 10_000
        assert await svc.get_balance(8) == 40_000

    asyncio.run(scenario())


def test_financial_hold_requires_manual_release_after_wallet_recovers(tmp_path, monkeypatch):
    svc = PaygService(str(tmp_path / "hold.db"))
    _operator(monkeypatch, GIB)

    async def scenario():
        account_id = await _account(
            svc, status="suspended", used=GIB, cumulative=GIB, charged=10_000,
        )
        await svc.credit_wallet(8, 50_000, kind="seed")
        billed = await svc.settle_usage(account_id, GIB)
        assert billed.new_status == "suspended"
        assert billed.charged_now_toman == 0
        with pytest.raises(PaygError, match="operator permission"):
            await svc.release_financial_hold(account_id, 8)
        assert await svc.release_financial_hold(account_id, 9001)
        assert (await svc.get_account(account_id))["status"] == "active"
        with pytest.raises(PaygError, match="does not have"):
            await svc.release_financial_hold(account_id, 9001)

    asyncio.run(scenario())


def test_concurrent_duplicate_snapshot_debits_wallet_only_once(tmp_path):
    svc = PaygService(str(tmp_path / "concurrent.db"))

    async def scenario():
        account_id = await _account(svc)
        await svc.credit_wallet(8, 100_000, kind="seed")
        results = await asyncio.gather(
            svc.settle_usage(account_id, GIB),
            svc.settle_usage(account_id, GIB),
        )
        assert sum(row.charged_now_toman for row in results) == 10_000
        assert await svc.get_balance(8) == 90_000
        history = await svc.wallet_history(8)
        assert len([row for row in history if row["kind"] == "payg_usage"]) == 1

    asyncio.run(scenario())
