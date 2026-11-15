"""Customer PAYG/FIXED classification and wallet reporting regressions."""
import sqlite3
from unittest.mock import AsyncMock

import pytest

import payg_customer_reporting as reports
from payg_offers_service import PaygOfferService
from payg_service import PaygService, GIB
from test_payg_historical_accrual import seed_account


@pytest.mark.asyncio
async def test_wallet_summary_uses_existing_ledger_and_never_mutates_balance(tmp_path,monkeypatch):
    service=PaygService(str(tmp_path/"wallet.db"))
    monkeypatch.setattr(reports,"payg_service",service)
    monkeypatch.setattr(reports,"payg_offer_service",PaygOfferService(service.db_path))
    await service.ensure_schema()
    account_id=seed_account(service.db_path,raw=GIB,cumulative=GIB,rate=1000,charged=1000)
    await service.credit_wallet(42,6000,kind="topup",reference_type="payg_topup",reference_id=1)
    await service.debit_wallet(42,1000,kind="payg_usage",reference_type="payg_account",reference_id=account_id)
    before=await service.get_balance(42)
    snapshot=await reports.wallet_summary(42)
    assert snapshot["balance"]==5000
    assert snapshot["topups"]==6000
    assert snapshot["usage"]==1000
    assert snapshot["debt"]==0
    assert snapshot["last_debit"]["amount_toman"]==-1000
    for category in ("all","topup","payg_usage","refund","adjustment","other"):
        await reports.ledger_page(42,category)
    assert await service.get_balance(42)==before
    with sqlite3.connect(service.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM wallet_ledger").fetchone()[0]==2


@pytest.mark.asyncio
async def test_ledger_pagination_filters_and_customer_isolation(tmp_path,monkeypatch):
    service=PaygService(str(tmp_path/"ledger.db"))
    monkeypatch.setattr(reports,"payg_service",service)
    monkeypatch.setattr(reports,"payg_offer_service",PaygOfferService(service.db_path))
    await service.ensure_schema()
    for n in range(11):
        await service.credit_wallet(42,100,kind="topup",reference_type="test",reference_id=n)
    await service.credit_wallet(43,999,kind="topup",reference_type="test",reference_id=99)
    first,more=await reports.ledger_page(42,"topup",page=0,size=8)
    second,last=await reports.ledger_page(42,"topup",page=1,size=8)
    assert len(first)==8 and more
    assert len(second)==3 and not last
    assert all(row["user_id"]==42 for row in first+second)
    assert len({row["id"] for row in first+second})==11
    with pytest.raises(ValueError):
        await reports.ledger_page(42,"unknown")


@pytest.mark.asyncio
async def test_payg_account_mapping_and_reports_are_owner_scoped(tmp_path,monkeypatch):
    service=PaygService(str(tmp_path/"account.db"))
    monkeypatch.setattr(reports,"payg_service",service)
    monkeypatch.setattr(reports,"payg_offer_service",PaygOfferService(service.db_path))
    await service.ensure_schema()
    await reports.payg_offer_service.ensure_schema()
    account_id=seed_account(service.db_path,rate=2500)
    with sqlite3.connect(service.db_path) as conn:
        conn.execute(
            "INSERT INTO payg_account_services(account_id,provider,service_id)"
            "VALUES(?,'rebecca',7)",(account_id,)
        )
    account=await reports.account_for_customer(42,account_id)
    assert account["service_id"]==7
    assert await reports.account_for_customer(43,account_id) is None
    assert await reports.usage_report(43,account_id) is None
    result=await reports.usage_report(42,account_id)
    assert result["accrued"]==0
    assert result["audit"]==[]
    assert result["rates"]==[]


@pytest.mark.asyncio
async def test_legacy_payg_account_without_service_mapping_remains_visible(tmp_path,monkeypatch):
    service=PaygService(str(tmp_path/"legacy.db"))
    monkeypatch.setattr(reports,"payg_service",service)
    monkeypatch.setattr(reports,"payg_offer_service",PaygOfferService(service.db_path))
    await service.ensure_schema()
    account_id=seed_account(service.db_path,rate=1000)
    account=await reports.account_for_customer(42,account_id)
    assert account is not None
    assert account["service_id"] is None


@pytest.mark.asyncio
async def test_legacy_fixed_renewal_callbacks_are_captured_only_for_owned_payg(monkeypatch):
    from types import SimpleNamespace
    from handlers.payg_customer_center import LegacyFixedRenewalForPayg
    import handlers.payg_customer_center as center
    admin=SimpleNamespace(id=420,user_id=42)
    monkeypatch.setattr(center.db,"get_admin_by_id",AsyncMock(return_value=admin))
    monkeypatch.setattr(center.payg_service,"get_admin_account",AsyncMock(
        return_value={"id":1,"user_id":42,"admin_id":420}
    ))
    guard=LegacyFixedRenewalForPayg()
    for name in (
        "admin_renew_panel_420",
        "admin_full_renew_420",
        "admin_full_renew_plan_420_3",
        "admin_combo_renew_plan_420_3",
        "admin_reset_renew_plan_420_3",
        "admin_renew_traffic_420",
        "admin_renew_time_420",
        "admin_renew_users_420",
    ):
        assert await guard(SimpleNamespace(
            data=name,from_user=SimpleNamespace(id=42)
        )),name
    assert not await guard(SimpleNamespace(
        data="admin_full_renew_plan_420_3",from_user=SimpleNamespace(id=43)
    ))
    monkeypatch.setattr(center.payg_service,"get_admin_account",AsyncMock(return_value=None))
    assert not await guard(SimpleNamespace(
        data="admin_full_renew_plan_420_3",from_user=SimpleNamespace(id=42)
    ))


@pytest.mark.asyncio
async def test_customer_dashboard_is_read_only_and_scoped(tmp_path,monkeypatch):
    from types import SimpleNamespace
    import handlers.payg_customer_center as center
    service=PaygService(str(tmp_path/"dashboard.db"))
    monkeypatch.setattr(reports,"payg_service",service)
    monkeypatch.setattr(reports,"payg_offer_service",PaygOfferService(service.db_path))
    monkeypatch.setattr(center,"payg_service",service)
    await service.ensure_schema()
    await reports.payg_offer_service.ensure_schema()
    account_id=seed_account(service.db_path,rate=2500)
    with sqlite3.connect(service.db_path) as conn:
        conn.execute(
            "INSERT INTO payg_account_services(account_id,provider,service_id)"
            "VALUES(?,'rebecca',7)",(account_id,)
        )
    monkeypatch.setattr(center,"get_service_by_rebecca_id",AsyncMock(
        return_value=SimpleNamespace(display_name="ProMax")
    ))
    message=SimpleNamespace(edit_text=AsyncMock())
    await center.render_payg_dashboard(message,42,account_id)
    text=message.edit_text.call_args.args[0]
    assert "PAYG" in text and "ProMax" in text and "2,500" in text
    keyboard=message.edit_text.call_args.kwargs["reply_markup"]
    callbacks=[b.callback_data for row in keyboard.inline_keyboard for b in row]
    assert f"paygsvc:topup:a:7:{account_id}" in callbacks
    assert not any(x.startswith("admin_full_renew") for x in callbacks)
    await center.render_payg_dashboard(message,43,account_id)
    assert "متعلق به شما نیست" in message.edit_text.call_args.args[0]
    assert await service.get_balance(42)==0


@pytest.mark.asyncio
async def test_my_services_shows_fixed_and_payg_without_mixing_renewal(monkeypatch):
    from types import SimpleNamespace
    import handlers.payg_customer_center as center
    monkeypatch.setattr(center,"customer_accounts",AsyncMock(return_value=[
        {"id":1,"admin_id":420,"service_id":7}
    ]))
    monkeypatch.setattr(center,"get_service_by_rebecca_id",AsyncMock(
        return_value=SimpleNamespace(display_name="ProMax")
    ))
    monkeypatch.setattr(center.db,"get_admins_for_user",AsyncMock(return_value=[
        SimpleNamespace(id=420,admin_name="PAYG panel"),
        SimpleNamespace(id=421,admin_name="Fixed panel"),
    ]))
    monkeypatch.setattr(center.payg_service,"get_admin_account",AsyncMock(return_value=None))
    message=SimpleNamespace(edit_text=AsyncMock())
    await center.render_accounts(message,42)
    keyboard=message.edit_text.call_args.kwargs["reply_markup"]
    mapping={b.text:b.callback_data for row in keyboard.inline_keyboard for b in row}
    assert mapping["⚡ PAYG · ProMax"]=="paygcustomer:dashboard:1"
    assert mapping["📦 FIXED · Fixed panel"]=="admin_renew_panel_421"
    assert "admin_renew_panel_420" not in mapping.values()


@pytest.mark.asyncio
async def test_legacy_unmapped_account_recharge_uses_original_safe_topup_route(tmp_path,monkeypatch):
    from types import SimpleNamespace
    import handlers.payg_customer_center as center
    service=PaygService(str(tmp_path/"legacy-ui.db"))
    monkeypatch.setattr(reports,"payg_service",service)
    monkeypatch.setattr(reports,"payg_offer_service",PaygOfferService(service.db_path))
    monkeypatch.setattr(center,"payg_service",service)
    await service.ensure_schema()
    account_id=seed_account(service.db_path,rate=1200)
    message=SimpleNamespace(edit_text=AsyncMock())
    await center.render_payg_dashboard(message,42,account_id)
    keyboard=message.edit_text.call_args.kwargs["reply_markup"]
    callbacks=[button.callback_data for row in keyboard.inline_keyboard for button in row]
    assert f"payg:topup:{account_id}" in callbacks
    assert not any(x.startswith("admin_full_renew") for x in callbacks)
