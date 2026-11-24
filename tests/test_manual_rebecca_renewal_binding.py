"""Manual Rebecca admin renewal: service-specific quotes and safe full plans.

All tests are read-only/mock scenarios; acceptance in the real Rebecca admin
API requires checking that GET /api/admins exposes the assigned service ID.
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.types import InlineKeyboardButton

import renewal_policy as renewal
from renewal_policy import RenewalPolicy
from handlers import panel_experience as experience
from handlers import admin_handlers as legacy
from product_catalog import ProductCategory


def manual_admin(admin_id=8, *, quota=10*1024**3):
    return SimpleNamespace(
        id=admin_id, user_id=42, marzban_username="manual_rebecca",
        admin_name="Imported customer", origin_plan_id=None,
        max_total_traffic=quota, is_active=True,
        allow_incremental_renewal=None,
    )


def category():
    return ProductCategory(
        id=77, rebecca_service_id=14, name="ProMax",
        description="", is_active=True, sort_order=100,
    )


def plan(plan_id=91, service="14", price=50000):
    return SimpleNamespace(
        id=plan_id, name="ProMax 100GB/30d",
        rebecca_service_ids=service, price=price, is_active=True,
        traffic_limit_bytes=100*1024**3, time_limit_seconds=30*86400,
        max_users=10, allow_incremental_renewal=True,
    )


def callback(data, *, owner=42):
    return SimpleNamespace(
        data=data,
        from_user=SimpleNamespace(id=owner),
        message=SimpleNamespace(edit_text=AsyncMock()),
        answer=AsyncMock(),
        bot=SimpleNamespace(),
    )


def state():
    return SimpleNamespace(clear=AsyncMock(), update_data=AsyncMock())


async def fake_button(text, callback_data, **kwargs):
    return InlineKeyboardButton(text=text, callback_data=callback_data)


@pytest.mark.parametrize("remote,expected", [
    ({"services":[{"id":14}]},14),
    ({"services":[14]},14),
    ({"service_ids":[14]},14),
    ({"service_id":14},14),
    ({"service_id":"14"},14),
    ({"service_id":14,"services":[{"service_id":14}]},14),
    ({"services":[{"id":14},{"id":15}]},None),
    ({"service_id":14,"services":[15]},None),
    ({"services":[]},None),
    ({"service_ids":["unknown"]},None),
    ({"service_id":False},None),
    ({},None),
])
def test_unambiguous_manual_service_mapping(remote, expected):
    assert renewal._rebecca_admin_service_id(remote)==expected


@pytest.mark.asyncio
async def test_manual_admin_uses_exact_remote_service_policy_and_prices(monkeypatch):
    import rebecca_api
    remote=AsyncMock(return_value={"username":"manual_rebecca","services":[{"id":14}]})
    monkeypatch.setattr(rebecca_api.rebecca_api,"find_admin",remote)
    monkeypatch.setattr(renewal.product_catalog,"get_category_by_service",
                        AsyncMock(return_value=category()))
    monkeypatch.setattr(renewal,"get_policy",AsyncMock(return_value=RenewalPolicy(
        mode="legacy",per_gb_toman=7000,per_30days_toman=85000,per_user_toman=2500
    )))
    monkeypatch.setattr(renewal.db,"get_billing_rates",AsyncMock(return_value={
        "per_gb_toman":1200,"per_30days_toman":10000,"per_user_toman":1000
    }))
    admin=manual_admin()
    category_id,policy=await renewal.policy_for_admin(admin)
    rates=await renewal.rates_for_admin(admin,policy=policy)
    assert category_id==77
    assert rates=={
        "per_gb_toman":7000,"per_30days_toman":85000,"per_user_toman":2500
    }
    remote.assert_awaited_once_with("manual_rebecca")


@pytest.mark.asyncio
async def test_manual_admin_without_verified_mapping_never_guesses_category(monkeypatch):
    import rebecca_api
    monkeypatch.setattr(rebecca_api.rebecca_api,"find_admin",AsyncMock(return_value={
        "username":"manual_rebecca","services":[{"id":14},{"id":15}]
    }))
    getter=AsyncMock()
    monkeypatch.setattr(renewal.product_catalog,"get_category_by_service",getter)
    cat,policy=await renewal.policy_for_admin(manual_admin())
    assert cat is None
    assert policy.mode=="legacy"
    getter.assert_not_awaited()


@pytest.mark.asyncio
async def test_manually_added_fixed_dashboard_shows_specific_incremental_and_full_plan(monkeypatch):
    admin=manual_admin()
    monkeypatch.setattr(experience.db,"get_admin_by_id",AsyncMock(return_value=admin))
    monkeypatch.setattr(experience,"_redirect_payg_renew",AsyncMock(return_value=False))
    monkeypatch.setattr(experience,"_button",fake_button)
    monkeypatch.setattr(experience,"_admin_panel_identity",AsyncMock(return_value=("ProMax",None)))
    monkeypatch.setattr(experience,"policy_for_admin",
                        AsyncMock(return_value=(77,RenewalPolicy())))
    monkeypatch.setattr(experience,"rates_for_admin",AsyncMock(return_value={
        "per_gb_toman":7000,"per_30days_toman":85000,"per_user_toman":2500
    }))
    monkeypatch.setattr(experience.product_catalog,"get_category",AsyncMock(return_value=category()))
    monkeypatch.setattr(experience.product_catalog,"resolve_panel_icon_key",AsyncMock(return_value=None))
    monkeypatch.setattr(experience.product_catalog,"plans_for_category",AsyncMock(return_value=[plan()]))
    cb=callback("admin_renew_panel_8")
    await experience.canonical_renew_panel(cb,state())
    markup=cb.message.edit_text.await_args.kwargs["reply_markup"]
    labels=[b.text for row in markup.inline_keyboard for b in row]
    callbacks=[b.callback_data for row in markup.inline_keyboard for b in row]
    assert "حجم · 1GB = 7,000 ت" in labels
    assert "زمان · 30 روز = 85,000 ت" in labels
    assert "کاربر · 1 نفر = 2,500 ت" in labels
    assert "admin_full_renew_8" in callbacks
    assert "ProMax" in cb.message.edit_text.await_args.args[0]
    assert "Imported customer" not in cb.message.edit_text.await_args.args[0]


@pytest.mark.asyncio
async def test_full_plan_picker_works_without_origin_plan_and_lists_only_own_service(monkeypatch):
    admin=manual_admin()
    monkeypatch.setattr(experience.db,"get_admin_by_id",AsyncMock(return_value=admin))
    monkeypatch.setattr(experience,"_redirect_payg_renew",AsyncMock(return_value=False))
    monkeypatch.setattr(experience,"_button",fake_button)
    monkeypatch.setattr(experience,"policy_for_admin",AsyncMock(return_value=(77,RenewalPolicy())))
    monkeypatch.setattr(experience,"_category_for_admin",AsyncMock(return_value=category()))
    monkeypatch.setattr(experience.product_catalog,"plans_for_category",AsyncMock(return_value=[plan()]))
    monkeypatch.setattr(experience.product_catalog,"resolve_panel_icon_key",AsyncMock(return_value=None))
    cb=callback("admin_full_renew_8")
    await experience.canonical_full_renew(cb,state())
    keys=[b.callback_data for row in cb.message.edit_text.await_args.kwargs[
        "reply_markup"].inline_keyboard for b in row]
    assert "admin_full_renew_plan_8_91" in keys
    assert "admin_renew_panel_8" in keys
    assert "50,000" in cb.message.edit_text.await_args.args[0]


@pytest.mark.asyncio
async def test_legacy_full_plan_callback_cannot_buy_plan_of_another_rebecca_service(monkeypatch):
    monkeypatch.setattr(legacy.config,"PANEL_PROVIDER","rebecca")
    admin=manual_admin()
    other_plan=plan(92,service="15")
    monkeypatch.setattr(legacy.db,"get_admin_by_id",AsyncMock(return_value=admin))
    monkeypatch.setattr(legacy.db,"get_plan_by_id",AsyncMock(return_value=other_plan))
    monkeypatch.setattr(legacy,"policy_for_admin",AsyncMock(return_value=(77,RenewalPolicy())))
    monkeypatch.setattr(legacy.product_catalog if hasattr(legacy,"product_catalog") else renewal.product_catalog,
                        "plans_for_category",AsyncMock(return_value=[plan()]))
    create=AsyncMock()
    monkeypatch.setattr(legacy.db,"add_order",create)
    cb=callback("admin_full_renew_plan_8_92")
    await legacy.admin_full_renew_plan_selected(cb)
    assert "متعلق" in cb.answer.await_args.args[0]
    create.assert_not_awaited()


@pytest.mark.asyncio
async def test_full_plan_callback_fails_closed_if_manual_service_unverifiable(monkeypatch):
    monkeypatch.setattr(legacy.config,"PANEL_PROVIDER","rebecca")
    admin=manual_admin()
    monkeypatch.setattr(legacy.db,"get_admin_by_id",AsyncMock(return_value=admin))
    monkeypatch.setattr(legacy.db,"get_plan_by_id",AsyncMock(return_value=plan()))
    monkeypatch.setattr(legacy,"policy_for_admin",AsyncMock(return_value=(None,RenewalPolicy())))
    create=AsyncMock()
    monkeypatch.setattr(legacy.db,"add_order",create)
    cb=callback("admin_full_renew_plan_8_91")
    await legacy.admin_full_renew_plan_selected(cb)
    assert "قابل تأیید نیست" in cb.answer.await_args.args[0]
    create.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_service_mapping_refuses_global_rate_quote(monkeypatch):
    admin=manual_admin()
    monkeypatch.setattr(experience.db,"get_admin_by_id",AsyncMock(return_value=admin))
    monkeypatch.setattr(experience,"_redirect_payg_renew",AsyncMock(return_value=False))
    monkeypatch.setattr(experience,"policy_for_admin",AsyncMock(return_value=(None,RenewalPolicy())))
    rates=AsyncMock()
    monkeypatch.setattr(experience,"rates_for_admin",rates)
    cb=callback("admin_renew_panel_8")
    await experience.canonical_renew_panel(cb,state())
    assert "مشخص نیست" in cb.answer.await_args.args[0]
    rates.assert_not_awaited()
    cb.message.edit_text.assert_not_awaited()
