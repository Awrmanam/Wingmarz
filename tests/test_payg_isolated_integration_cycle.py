"""Isolated end-to-end PAYG lifecycle against a stateful fake Rebecca boundary.

Not staging: real SQLite finance/provisioning/approval/scheduler logic is used;
only Rebecca HTTP and the legacy local-admin adapter are replaced.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import payg_service as core_mod
import payg_offers_service as offers_mod
from payg_service import GIB, PaygError, PaygService


class FakeRebecca:
    def __init__(self):
        self.admins = {}
        self.create_count = 0
        self.disable_count = 0
        self.enable_count = 0

    async def find_admin(self, username):
        return self.admins.get(username)

    async def create_admin_verified(self, username, password, user_id, **kwargs):
        assert username not in self.admins
        self.create_count += 1
        self.admins[username] = {
            "username": username, "status": "active",
            "telegram_id": user_id, "usage": 0,
        }

    async def get_admin_usage(self, username):
        return self.admins[username]["usage"]

    async def disable_admin(self, username, reason):
        self.disable_count += 1
        self.admins[username]["status"] = "disabled"

    async def enable_admin(self, username):
        self.enable_count += 1
        self.admins[username]["status"] = "active"


@pytest.mark.asyncio
async def test_four_blocked_scenarios_as_one_isolated_integration_cycle(tmp_path,monkeypatch):
    path = str(tmp_path / "complete-cycle.db")
    core = PaygService(path)
    offers = offers_mod.PaygOfferService(path)
    monkeypatch.setattr(offers_mod,"payg_service",core)
    monkeypatch.setattr(core_mod.config,"SUDO_ADMINS",[99])
    catalog = SimpleNamespace(is_enabled=True,display_name="Isolated Rebecca service")
    monkeypatch.setattr(offers_mod,"get_service_by_rebecca_id",AsyncMock(return_value=catalog))
    remote = FakeRebecca()
    for name in ("find_admin","create_admin_verified","get_admin_usage",
                 "disable_admin","enable_admin"):
        monkeypatch.setattr(core_mod.rebecca_api,name,getattr(remote,name))
    local = {}
    async def get_by_name(username):
        return local.get(username)
    async def add_admin(model):
        local[model.marzban_username] = SimpleNamespace(
            id=70,user_id=model.user_id,marzban_username=model.marzban_username,
            is_active=True,deactivated_reason="",
        )
        return True
    async def get_by_id(admin_id):
        return next((x for x in local.values() if x.id==admin_id),None)
    async def deactivate(admin_id,reason):
        admin = await get_by_id(admin_id)
        admin.is_active=False
        admin.deactivated_reason=reason
        return True
    async def reactivate(admin_id):
        admin=await get_by_id(admin_id)
        admin.is_active=True
        admin.deactivated_reason=""
        return True
    monkeypatch.setattr(offers_mod.db,"get_admin_by_marzban_username",get_by_name)
    monkeypatch.setattr(offers_mod.db,"add_admin",add_admin)
    monkeypatch.setattr(core_mod.db,"get_admin_by_id",get_by_id)
    monkeypatch.setattr(core_mod.db,"deactivate_admin",deactivate)
    monkeypatch.setattr(core_mod.db,"reactivate_admin",reactivate)

    await offers.ensure_offer(7)
    await offers.update_offer(7,enabled=True,price_per_gb_toman=1000,min_topup_toman=1)
    first = await offers.create_topup(42,7,5000,source="integration")
    assert await core.submit_topup_receipt(first,42,"fake-receipt")
    with pytest.raises(PaygError,match="operator permission"):
        await offers.approve_topup(first,42)
    purchase = await offers.approve_topup(first,99)
    account_id = purchase["account"]["id"]
    assert purchase["account"]["user_id"]==42
    assert remote.create_count==1
    assert await core.get_balance(42)==5000
    repeated = await offers.approve_topup(first,99)
    assert repeated["account"]["id"]==account_id
    assert remote.create_count==1
    assert await core.get_balance(42)==5000

    username = "payg42_7"
    remote.admins[username]["usage"]=2*GIB
    usage = await core.sync_account(account_id)
    assert usage.charged_now_toman==2000
    assert usage.new_status=="active"
    assert await core.get_balance(42)==3000

    remote.admins[username]["usage"]=6*GIB
    exhausted = await core.sync_account(account_id)
    assert exhausted.charged_now_toman==3000
    assert exhausted.new_status=="suspended"
    assert remote.admins[username]["status"]=="disabled"
    assert remote.disable_count==1
    assert await core.get_balance(42)==0

    recharge = await offers.create_topup(42,7,3000,source="integration",account_id=account_id)
    assert await core.submit_topup_receipt(recharge,42,"fake-recharge")
    result = await offers.approve_topup(recharge,99)
    assert result["billing"].charged_now_toman==1000
    assert result["billing"].new_status=="suspended"
    assert await core.get_balance(42)==2000
    assert remote.admins[username]["status"]=="disabled"
    assert await core.release_financial_hold(account_id,99)
    resumed = await core.sync_account(account_id)
    assert resumed.new_status=="active"
    assert remote.admins[username]["status"]=="active"
    assert remote.enable_count==1
    assert await core.get_balance(42)==2000
    assert await core.get_accrued_target(account_id)==6000
    ledger=await core.wallet_history(42)
    assert sum(x["amount_toman"] for x in ledger)==2000
    assert len([x for x in ledger if x["kind"]=="topup"])==2
    assert len([x for x in ledger if x["kind"]=="payg_usage"])==3


@pytest.mark.asyncio
async def test_topup_cannot_target_another_customer_account_or_migration(tmp_path):
    import aiosqlite
    core=PaygService(str(tmp_path/"ownership.db"))
    await core.ensure_schema()
    async with aiosqlite.connect(core.db_path) as conn:
        await conn.execute(
            "INSERT INTO payg_accounts(user_id,admin_id,provider,status,price_per_gb_toman)"
            "VALUES(42,70,'rebecca','active',1000)"
        )
        await conn.execute(
            "INSERT INTO payg_migrations(user_id,account_id,target_plan_id,price_toman)"
            "VALUES(42,1,2,3000)"
        )
        await conn.commit()
    with pytest.raises(PaygError,match="does not belong"):
        await core.create_topup(43,1000,account_id=1)
    with pytest.raises(PaygError,match="does not belong"):
        await core.create_topup(43,1000,migration_id=1)
    with pytest.raises(PaygError,match="mismatch"):
        await core.create_topup(42,1000,account_id=999,migration_id=1)
    assert await core.create_topup(42,1000,account_id=1,migration_id=1)>0
