import asyncio

import aiosqlite
import pytest

from payg_service import GIB, PaygInsufficientFunds, PaygService


def run(coro):
    return asyncio.run(coro)


def test_wallet_credit_debit_and_ledger_are_consistent(tmp_path):
    service = PaygService(str(tmp_path / "payg.db"))

    async def scenario():
        await service.ensure_schema()
        assert await service.get_balance(42) == 0
        assert await service.credit_wallet(42, 200_000, kind="test") == 200_000
        assert await service.debit_wallet(42, 75_000, kind="test_debit") == 125_000
        assert await service.get_balance(42) == 125_000
        history = await service.wallet_history(42)
        assert [row["amount_toman"] for row in history[:2]] == [-75_000, 200_000]
        assert history[0]["balance_after_toman"] == 125_000

    run(scenario())


def test_wallet_never_goes_negative(tmp_path):
    service = PaygService(str(tmp_path / "payg.db"))

    async def scenario():
        await service.credit_wallet(7, 10_000, kind="test")
        with pytest.raises(PaygInsufficientFunds) as exc:
            await service.debit_wallet(7, 10_001, kind="too_much")
        assert exc.value.shortfall_toman == 1
        assert await service.get_balance(7) == 10_000

    run(scenario())


def test_usage_billing_is_cumulative_and_rounding_safe(tmp_path):
    path = str(tmp_path / "payg.db")
    service = PaygService(path)

    async def scenario():
        await service.ensure_schema()
        await service.credit_wallet(5, 100_000, kind="seed")
        async with aiosqlite.connect(path) as conn:
            cur = await conn.execute(
                """
                INSERT INTO payg_accounts(
                    user_id,admin_id,provider,status,price_per_gb_toman,last_raw_usage_bytes
                ) VALUES(5,99,'rebecca','active',10000,0)
                """
            )
            account_id = cur.lastrowid
            await conn.commit()

        # Half a GiB costs exactly 5,000 Toman.
        first = await service.settle_usage(account_id, GIB // 2)
        assert first.charged_now_toman == 5_000
        assert first.balance_toman == 95_000

        # Another quarter GiB brings cumulative charge to 7,500, not a
        # separately rounded approximation.
        second = await service.settle_usage(account_id, (3 * GIB) // 4)
        assert second.charged_now_toman == 2_500
        assert second.charged_total_toman == 7_500
        assert second.balance_toman == 92_500

    run(scenario())


def test_usage_counter_reset_is_added_not_subtracted(tmp_path):
    path = str(tmp_path / "payg.db")
    service = PaygService(path)

    async def scenario():
        await service.ensure_schema()
        await service.credit_wallet(9, 100_000, kind="seed")
        async with aiosqlite.connect(path) as conn:
            cur = await conn.execute(
                """
                INSERT INTO payg_accounts(
                    user_id,admin_id,provider,status,price_per_gb_toman,
                    last_raw_usage_bytes,cumulative_usage_bytes,charged_toman_total
                ) VALUES(9,77,'rebecca','active',10000,?,?,?)
                """,
                (2 * GIB, 2 * GIB, 20_000),
            )
            account_id = cur.lastrowid
            await conn.commit()

        # Provider counter reset to 0.5 GiB: cumulative must become 2.5 GiB.
        result = await service.settle_usage(account_id, GIB // 2)
        assert result.cumulative_usage_bytes == (5 * GIB) // 2
        assert result.charged_now_toman == 5_000

    run(scenario())


def test_exhausted_wallet_suspends_and_keeps_outstanding_debt(tmp_path):
    path = str(tmp_path / "payg.db")
    service = PaygService(path)

    async def scenario():
        await service.ensure_schema()
        await service.credit_wallet(3, 1_000, kind="seed")
        async with aiosqlite.connect(path) as conn:
            cur = await conn.execute(
                """INSERT INTO payg_accounts(
                    user_id,admin_id,provider,status,price_per_gb_toman
                ) VALUES(3,33,'rebecca','active',10000)"""
            )
            account_id = cur.lastrowid
            await conn.commit()

        result = await service.settle_usage(account_id, GIB)
        assert result.new_status == "suspended"
        assert result.balance_toman == 0
        assert result.charged_now_toman == 1_000
        assert result.outstanding_toman == 9_000

    run(scenario())


def test_topup_approval_is_idempotent(tmp_path):
    service = PaygService(str(tmp_path / "payg.db"))

    async def scenario():
        topup_id = await service.create_topup(123, 50_000, purpose="wallet")
        assert await service.submit_topup_receipt(topup_id, 123, "file-id") is True
        await service.approve_topup(topup_id, 999)
        assert await service.get_balance(123) == 50_000
        # A repeated Telegram callback must never double-credit.
        await service.approve_topup(topup_id, 999)
        assert await service.get_balance(123) == 50_000
        history = await service.wallet_history(123)
        assert len([row for row in history if row["kind"] == "topup"]) == 1

    run(scenario())


def test_payg_routers_precede_legacy_marketplace():
    source = open("handlers/__init__.py", encoding="utf-8").read()
    assert source.index("include_router(payg_entry_router)") < source.index("include_router(service_marketplace_router)")
    assert source.index("include_router(payg_router)") < source.index("include_router(service_marketplace_router)")
