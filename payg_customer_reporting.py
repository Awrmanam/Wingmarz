"""Read-only customer finance and PAYG reports on the existing ledger.

No new wallet, balances, backfills, Rebecca guesses, or financial mutations.
"""
from __future__ import annotations

import aiosqlite

from payg_service import payg_service


async def customer_accounts(user_id: int) -> list[dict]:
    await payg_service.ensure_schema()
    async with aiosqlite.connect(payg_service.db_path) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            "SELECT a.*,s.service_id FROM payg_accounts a "
            "LEFT JOIN payg_account_services s ON s.account_id=a.id "
            "WHERE a.user_id=? AND a.provider='rebecca' AND a.status!='migrated' "
            "ORDER BY a.id DESC",(int(user_id),),
        ) as cur:
            return [dict(row) for row in await cur.fetchall()]


async def account_for_customer(user_id: int, account_id: int) -> dict | None:
    return next((a for a in await customer_accounts(user_id)
                 if int(a["id"])==int(account_id)),None)


async def wallet_summary(user_id: int) -> dict:
    await payg_service.ensure_schema()
    async with aiosqlite.connect(payg_service.db_path) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            "SELECT COALESCE(SUM(CASE WHEN kind='topup' THEN amount_toman ELSE 0 END),0) "
            "AS topups, COALESCE(SUM(CASE WHEN kind='payg_usage' THEN -amount_toman ELSE 0 END),0) "
            "AS usage, MAX(created_at) AS last_update FROM wallet_ledger WHERE user_id=?",
            (int(user_id),),
        ) as cur:
            totals=dict(await cur.fetchone())
        async with conn.execute(
            "SELECT * FROM wallet_ledger WHERE user_id=? AND kind='payg_usage' "
            "ORDER BY id DESC LIMIT 1",(int(user_id),),
        ) as cur:
            last=await cur.fetchone()
    debts=[]
    for account in await customer_accounts(user_id):
        try:
            accrued=await payg_service.get_accrued_target(int(account["id"]))
            debts.append(max(0,accrued-int(account["charged_toman_total"])))
        except Exception:
            debts.append(None)
    return {
        "balance":await payg_service.get_balance(user_id),
        "topups":int(totals["topups"]),
        "usage":int(totals["usage"]),
        "debt":sum(x for x in debts if x is not None),
        "debt_complete":all(x is not None for x in debts),
        "last_debit":dict(last) if last else None,
        "last_update":totals["last_update"],
    }


async def ledger_page(user_id: int, category: str="all", page: int=0, size: int=8) -> tuple[list[dict],bool]:
    if category not in {"all","topup","payg_usage","refund","adjustment","other"}:
        raise ValueError("unknown ledger category")
    if not 0 <= int(page) <= 10000:
        raise ValueError("invalid ledger page")
    await payg_service.ensure_schema()
    where="user_id=?"
    params=[int(user_id)]
    if category in {"topup","payg_usage"}:
        where+=" AND kind=?"
        params.append(category)
    elif category=="refund":
        where+=" AND (kind LIKE '%refund%' OR kind LIKE '%reversal%')"
    elif category=="adjustment":
        where+=" AND (kind LIKE '%adjust%' OR kind LIKE '%correction%')"
    elif category=="other":
        where+=" AND kind NOT IN ('topup','payg_usage') AND kind NOT LIKE '%refund%' AND kind NOT LIKE '%reversal%' AND kind NOT LIKE '%adjust%' AND kind NOT LIKE '%correction%'"
    params.extend([int(size)+1,int(page)*int(size)])
    async with aiosqlite.connect(payg_service.db_path) as conn:
        conn.row_factory=aiosqlite.Row
        async with conn.execute(
            f"SELECT * FROM wallet_ledger WHERE {where} ORDER BY id DESC LIMIT ? OFFSET ?",
            params,
        ) as cur:
            rows=[dict(row) for row in await cur.fetchall()]
    return rows[:size],len(rows)>size


async def usage_report(user_id: int, account_id: int) -> dict | None:
    account=await account_for_customer(user_id,account_id)
    if not account:
        return None
    await payg_service.ensure_schema()
    async with aiosqlite.connect(payg_service.db_path) as conn:
        conn.row_factory=aiosqlite.Row
        async with conn.execute(
            "SELECT * FROM payg_usage_audit WHERE account_id=? ORDER BY id DESC LIMIT 25",
            (int(account_id),),
        ) as cur:
            audit=[dict(row) for row in await cur.fetchall()]
        async with conn.execute(
            "SELECT * FROM payg_rate_history WHERE account_id=? ORDER BY id DESC LIMIT 15",
            (int(account_id),),
        ) as cur:
            rates=[dict(row) for row in await cur.fetchall()]
        async with conn.execute(
            "SELECT last_error FROM payg_discount_targets WHERE account_id=?",
            (int(account_id),),
        ) as cur:
            pending=await cur.fetchone()
    try:
        accrued=await payg_service.get_accrued_target(account_id)
    except Exception:
        accrued=None
    return {"account":account,"audit":audit,"rates":rates,
            "accrued":accrued,"discount":await payg_service.get_customer_discount(account_id),
            "hold":str(pending[0]) if pending and pending[0] else None}
