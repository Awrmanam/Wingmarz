"""Regression audit: PAYG legacy safety, service boundaries and customer UX."""
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from payg_service import PaygError, PaygService, GIB
from payg_offers_service import PaygOfferService, PaygOfferError
import payg_offers_service as offer_module
import handlers.payg_customer_center as center
from test_payg_historical_accrual import seed_account


@pytest.mark.asyncio
async def test_old_payg_create_cannot_call_rebecca_or_delete_remote(tmp_path,monkeypatch):
    from payg_service import rebecca_api
    service=PaygService(str(tmp_path/"legacy.db"))
    create=AsyncMock()
    delete=AsyncMock()
    monkeypatch.setattr(rebecca_api,"create_admin_verified",create)
    monkeypatch.setattr(rebecca_api,"delete_admin",delete)
    with pytest.raises(PaygError,match="legacy PAYG creation retired"):
        await service.ensure_payg_account(42)
    create.assert_not_awaited()
    delete.assert_not_awaited()
    assert await service.get_balance(42)==0


@pytest.mark.asyncio
async def test_disabled_offer_allows_legacy_customer_recharge_but_no_new_purchase(tmp_path,monkeypatch):
    path=str(tmp_path/"offers.db")
    core=PaygService(path)
    offer=PaygOfferService(path)
    monkeypatch.setattr(offer_module,"payg_service",core)
    monkeypatch.setattr(offer_module,"get_service_by_rebecca_id",AsyncMock(
        return_value=SimpleNamespace(display_name="ProMax",is_enabled=True)
    ))
    await offer.ensure_offer(7)
    await offer.update_offer(7,enabled=True,price_per_gb_toman=2500,min_topup_toman=1)
    account_id=seed_account(path,rate=2500)
    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT INTO payg_account_services(account_id,service_id) VALUES(?,7)",(account_id,)
        )
    await offer.update_offer(7,enabled=False)
    topup_id=await offer.create_topup(42,7,3000,source="test",account_id=account_id)
    assert topup_id>0
    with pytest.raises(PaygOfferError,match="فروش جدید"):
        await offer.create_topup(42,7,3000,source="test")
    with pytest.raises(PaygOfferError,match="ownership"):
        await offer.create_topup(43,7,3000,source="test",account_id=account_id)


@pytest.mark.asyncio
async def test_migration_rejects_different_service_and_unmapped_history(tmp_path,monkeypatch):
    from payg_service import db
    path=str(tmp_path/"conversion.db")
    service=PaygService(path)
    await service.ensure_schema()
    account_id=seed_account(path)
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE payg_account_services(account_id INTEGER PRIMARY KEY,provider TEXT DEFAULT 'rebecca',service_id INTEGER)")
        conn.execute("INSERT INTO payg_account_services(account_id,service_id) VALUES(?,7)",(account_id,))
    wrong_plan=SimpleNamespace(id=33,is_active=True,price=2000,rebecca_service_ids="8")
    monkeypatch.setattr(db,"get_plan_by_id",AsyncMock(return_value=wrong_plan))
    with pytest.raises(PaygError,match="another Rebecca service"):
        await service.prepare_migration(42,account_id,33)
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM payg_migrations").fetchone()[0]==0
        conn.execute("DELETE FROM payg_account_services")
    with pytest.raises(PaygError,match="mapping must be verified"):
        await service.prepare_migration(42,account_id,33)


@pytest.mark.asyncio
async def test_legacy_fixed_callback_for_retired_payg_cannot_create_order(monkeypatch):
    admin=SimpleNamespace(id=420,user_id=42)
    monkeypatch.setattr(center.db,"get_admin_by_id",AsyncMock(return_value=admin))
    monkeypatch.setattr(center.payg_service,"get_admin_account",AsyncMock(return_value=None))
    monkeypatch.setattr(center,"historical_payg_admin_ids",AsyncMock(return_value={420}))
    callback=SimpleNamespace(data="admin_full_renew_plan_420_3",
        from_user=SimpleNamespace(id=42),
        message=SimpleNamespace(edit_text=AsyncMock()),answer=AsyncMock())
    guard=center.LegacyFixedRenewalForPayg()
    assert await guard(callback)
    render=AsyncMock()
    monkeypatch.setattr(center,"render_accounts",render)
    state=SimpleNamespace(clear=AsyncMock())
    await center.redirect_legacy_fixed_renewal(callback,state)
    render.assert_awaited_once_with(callback.message,42)
    callback.answer.assert_awaited_once()
    assert callback.answer.await_args.kwargs["show_alert"]


@pytest.mark.asyncio
async def test_customer_approval_notice_is_reserved_once_even_with_duplicate_clicks(tmp_path,monkeypatch):
    import payg_service as mod
    service=PaygService(str(tmp_path/"notifications.db"))
    monkeypatch.setattr(mod.config,"SUDO_ADMINS",[99])
    topup_id=await service.create_topup(42,2500,purpose="wallet")
    await service.submit_topup_receipt(topup_id,42,"test-file-id")
    await service.approve_topup(topup_id,99)
    bot=SimpleNamespace(send_message=AsyncMock())
    assert await service.send_approval_notice_once(bot,topup_id=topup_id,user_id=42,message="ok")
    assert not await service.send_approval_notice_once(bot,topup_id=topup_id,user_id=42,message="again")
    bot.send_message.assert_awaited_once_with(42,"ok")
    with sqlite3.connect(service.db_path) as conn:
        assert conn.execute("SELECT status FROM payg_approval_notices WHERE topup_id=?",(topup_id,)).fetchone()[0]=="sent"


@pytest.mark.asyncio
async def test_approval_notification_timeout_is_not_blindly_resent(tmp_path,monkeypatch):
    import payg_service as mod
    service=PaygService(str(tmp_path/"uncertain.db"))
    monkeypatch.setattr(mod.config,"SUDO_ADMINS",[99])
    topup_id=await service.create_topup(42,2500)
    await service.submit_topup_receipt(topup_id,42,"test-file-id")
    await service.approve_topup(topup_id,99)
    bot=SimpleNamespace(send_message=AsyncMock(side_effect=TimeoutError("ambiguous")))
    assert not await service.send_approval_notice_once(bot,topup_id=topup_id,user_id=42,message="ok")
    assert not await service.send_approval_notice_once(bot,topup_id=topup_id,user_id=42,message="ok")
    assert bot.send_message.await_count==1
    with sqlite3.connect(service.db_path) as conn:
        assert conn.execute("SELECT status FROM payg_approval_notices WHERE topup_id=?",(topup_id,)).fetchone()[0]=="unknown"


@pytest.mark.asyncio
async def test_customer_fixed_dashboard_and_contextual_ledger_navigation(monkeypatch):
    monkeypatch.setattr(center.db,"get_admin_by_id",AsyncMock(
        return_value=SimpleNamespace(id=421,user_id=42,admin_name="ProMax",is_active=True)
    ))
    monkeypatch.setattr(center.payg_service,"get_admin_account",AsyncMock(return_value=None))
    monkeypatch.setattr(center,"historical_payg_admin_ids",AsyncMock(return_value=set()))
    async def basic_markup(rows):
        return InlineKeyboardMarkup(inline_keyboard=rows)
    monkeypatch.setattr(center,"_styled_markup",basic_markup)
    msg=SimpleNamespace(edit_text=AsyncMock())
    assert await center.render_fixed_dashboard(msg,42,421)
    keys=[b.callback_data for row in msg.edit_text.await_args.kwargs["reply_markup"].inline_keyboard for b in row]
    assert {"admin_renew_panel_421","info_panel_421","wallet:accounts"}.issubset(keys)
    assert not any(x.startswith("paygsvc:topup") for x in keys)
    assert not await center.render_fixed_dashboard(msg,43,421)

    monkeypatch.setattr(center,"account_for_customer",AsyncMock(return_value={"id":7,"user_id":42}))
    monkeypatch.setattr(center,"ledger_page",AsyncMock(return_value=([],False)))
    callback=SimpleNamespace(data="wallet:ledger:all:0:payg:7",
        from_user=SimpleNamespace(id=42),message=msg,answer=AsyncMock())
    await center.wallet_ledger(callback)
    keys=[b.callback_data for row in msg.edit_text.await_args.kwargs["reply_markup"].inline_keyboard for b in row]
    assert "wallet:ledger:topup:0:payg:7" in keys
    assert "paygcustomer:dashboard:7" in keys


@pytest.mark.asyncio
async def test_missing_owner_blocks_only_discount_accounts(tmp_path,monkeypatch):
    service=PaygService(str(tmp_path/"owner.db"))
    await service.ensure_schema()
    affected=seed_account(service.db_path)
    with sqlite3.connect(service.db_path) as conn:
        conn.execute(
            "INSERT INTO payg_accounts(user_id,admin_id,provider,status,price_per_gb_toman) "
            "VALUES(43,430,'rebecca','active',1000)"
        )
        conn.execute(
            "INSERT INTO payg_discount_targets(account_id,base_rate) VALUES(?,1000)",
            (affected,)
        )
    monkeypatch.delenv("PAYG_OWNER_ID",raising=False)
    result=await service.reconcile_customer_discounts()
    assert result=={affected:"manual_review"}


@pytest.mark.asyncio
async def test_migration_selector_skips_malformed_unrelated_plans(tmp_path,monkeypatch):
    from payg_service import db
    path=str(tmp_path/"plans.db")
    service=PaygService(path)
    await service.ensure_schema()
    account_id=seed_account(path)
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE payg_account_services(account_id INTEGER PRIMARY KEY,provider TEXT DEFAULT 'rebecca',service_id INTEGER)")
        conn.execute("INSERT INTO payg_account_services(account_id,service_id) VALUES(?,7)",(account_id,))
    bad=SimpleNamespace(id=3,is_active=True,price=1000,rebecca_service_ids="not-numbers")
    correct=SimpleNamespace(id=4,is_active=True,price=1000,rebecca_service_ids="7")
    unrelated=SimpleNamespace(id=5,is_active=True,price=1000,rebecca_service_ids="8")
    monkeypatch.setattr(db,"get_plans",AsyncMock(return_value=[bad,correct,unrelated]))
    plans=await service.migration_plans_for_account(account_id)
    assert [p.id for p in plans]==[4]
    with pytest.raises(PaygError,match="invalid Rebecca service IDs"):
        await service._assert_migration_plan_matches(account_id,bad)
