import asyncio

import aiosqlite

from payg_service import GIB, PaygService
from payg_usage_alerts import PaygUsageAlertService


def run(coro):
    return asyncio.run(coro)


def test_existing_accounts_are_baselined_without_retroactive_spam(tmp_path):
    path = str(tmp_path / "payg.db")
    billing = PaygService(path)
    alerts = PaygUsageAlertService(path)

    async def scenario():
        await billing.ensure_schema()
        await billing.credit_wallet(1, 500_000, kind="seed")
        async with aiosqlite.connect(path) as conn:
            cur = await conn.execute(
                """
                INSERT INTO payg_accounts(
                    user_id,admin_id,provider,status,price_per_gb_toman,
                    cumulative_usage_bytes,charged_toman_total
                ) VALUES(1,101,'rebecca','active',10000,?,250000)
                """,
                (25 * GIB,),
            )
            account_id = int(cur.lastrowid)
            await conn.commit()

        await alerts.ensure_schema()
        assert await alerts.pending_alerts(account_id) == []
        state = await alerts.get_state(account_id)
        assert state is not None
        assert state["last_milestone_gb"] == 20

    run(scenario())


def test_new_account_alerts_at_each_10gb_milestone_and_does_not_repeat(tmp_path):
    path = str(tmp_path / "payg.db")
    billing = PaygService(path)
    alerts = PaygUsageAlertService(path)

    async def scenario():
        await billing.ensure_schema()
        # Initialize feature before the account exists: new accounts must start
        # their milestone tracking from zero rather than being backfilled.
        await alerts.ensure_schema()
        await billing.credit_wallet(2, 1_000_000, kind="seed")
        async with aiosqlite.connect(path) as conn:
            cur = await conn.execute(
                """
                INSERT INTO payg_accounts(
                    user_id,admin_id,provider,status,price_per_gb_toman,last_raw_usage_bytes
                ) VALUES(2,202,'rebecca','active',10000,0)
                """
            )
            account_id = int(cur.lastrowid)
            await conn.commit()

        await billing.settle_usage(account_id, 9 * GIB)
        assert await alerts.pending_alerts(account_id) == []

        await billing.settle_usage(account_id, 10 * GIB + 1)
        due = await alerts.pending_alerts(account_id)
        assert [item.milestone_gb for item in due] == [10]
        assert due[0].interval_cost_toman == 100_000
        assert due[0].charged_total_toman == 100_000
        assert due[0].balance_toman == 900_000

        await alerts.mark_sent(account_id, 10)
        assert await alerts.pending_alerts(account_id) == []

        await billing.settle_usage(account_id, 20 * GIB + 1)
        due = await alerts.pending_alerts(account_id)
        assert [item.milestone_gb for item in due] == [20]
        await alerts.mark_sent(account_id, 20)
        assert await alerts.pending_alerts(account_id) == []

    run(scenario())


def test_large_usage_jump_returns_every_crossed_10gb_threshold(tmp_path):
    path = str(tmp_path / "payg.db")
    billing = PaygService(path)
    alerts = PaygUsageAlertService(path)

    async def scenario():
        await billing.ensure_schema()
        await alerts.ensure_schema()
        await billing.credit_wallet(3, 2_000_000, kind="seed")
        async with aiosqlite.connect(path) as conn:
            cur = await conn.execute(
                """
                INSERT INTO payg_accounts(
                    user_id,admin_id,provider,status,price_per_gb_toman,last_raw_usage_bytes
                ) VALUES(3,303,'rebecca','active',10000,0)
                """
            )
            account_id = int(cur.lastrowid)
            await conn.commit()

        await billing.settle_usage(account_id, 31 * GIB)
        due = await alerts.pending_alerts(account_id)
        assert [item.milestone_gb for item in due] == [10, 20, 30]

    run(scenario())
