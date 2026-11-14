"""Category-level renewal modes, rates and pricing safety."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.types import InlineKeyboardButton

from database import Database
import renewal_policy as policy
from handlers import panel_experience as panels
from handlers import admin_handlers as legacy
from handlers import renewal_settings as editor
from rebecca_renewal import apply_rebecca_renewal


def run(coro):
    return asyncio.run(coro)


def category(category_id=3, service_id=10):
    return SimpleNamespace(id=category_id, name="Promax", rebecca_service_id=service_id)


def panel(*, unlimited=False):
    return SimpleNamespace(
        id=19, user_id=42, origin_plan_id=7, admin_name="seller",
        marzban_username="seller", is_active=True,
        max_total_traffic=0 if unlimited else 100 * 1024**3,
        max_total_time=30*86400, allow_incremental_renewal=None,
    )


def callback(data, user_id=42):
    return SimpleNamespace(
        data=data,
        from_user=SimpleNamespace(id=user_id),
        message=SimpleNamespace(edit_text=AsyncMock()),
        answer=AsyncMock(),
        bot=SimpleNamespace(),
    )


def state():
    return SimpleNamespace(clear=AsyncMock(), update_data=AsyncMock(), set_state=AsyncMock())


def test_policy_defaults_legacy_and_keeps_per_category_prices(tmp_path, monkeypatch):
    database = Database(str(tmp_path / "bot.db"))
    run(database.init_db())
    monkeypatch.setattr(policy, "db", database)
    assert run(policy.get_policy(3)).mode == "legacy"
    run(policy.save_mode(3, "choose"))
    run(policy.save_rate(3, "per_gb_toman", 12000))
    run(policy.save_rate(3, "per_30days_toman", 180000))
    run(policy.save_mode(4, "time"))
    assert run(policy.get_policy(3)).mode == "choose"
    assert run(policy.get_policy(3)).per_gb_toman == 12000
    assert run(policy.get_policy(3)).per_30days_toman == 180000
    assert run(policy.get_policy(4)).per_gb_toman is None
    assert run(policy.get_policy(4)).mode == "time"
    run(policy.save_rate(3, "per_gb_toman", None))
    assert run(policy.get_policy(3)).per_gb_toman is None


def test_policy_rejects_invalid_amount_or_mode(tmp_path, monkeypatch):
    database = Database(str(tmp_path / "bot.db"))
    run(database.init_db())
    monkeypatch.setattr(policy, "db", database)
    with pytest.raises(policy.RenewalPolicyError):
        run(policy.save_mode(3, "unapproved"))
    with pytest.raises(policy.RenewalPolicyError):
        run(policy.save_rate(3, "per_gb_toman", -1))
    with pytest.raises(policy.RenewalPolicyError):
        run(policy.save_rate(3, "per_30days_toman", True))
    with pytest.raises(policy.RenewalPolicyError):
        run(policy.save_rate(3, "wrong", 500))
    run(database.set_setting("renewal:category:3", '{"mode":"unknown"}'))
    with pytest.raises(policy.RenewalPolicyError):
        run(policy.get_policy(3))


def test_category_rate_isolated_and_ambiguous_plan_does_not_guess(monkeypatch):
    monkeypatch.setattr(policy.db, "get_plan_by_id", AsyncMock(return_value=SimpleNamespace(
        rebecca_service_ids="10,20"
    )))
    monkeypatch.setattr(policy.product_catalog, "categories",
                        AsyncMock(return_value=[category(3, 10), category(4, 20)]))
    assert run(policy._category_id_for_admin(panel())) is None
    monkeypatch.setattr(policy.product_catalog, "categories",
                        AsyncMock(return_value=[category(3, 10)]))
    assert run(policy._category_id_for_admin(panel())) == 3


def test_allowed_renewal_modes_are_explicit():
    assert policy.allows("legacy", "reset")
    assert policy.allows("both", "both")
    assert not policy.allows("both", "traffic")
    assert policy.allows("traffic", "traffic")
    assert not policy.allows("traffic", "time")
    assert policy.allows("time", "time")
    assert policy.allows("choose", "both")
    assert policy.allows("choose", "time")
    assert policy.allows("choose", "traffic")
    assert not policy.allows("choose", "reset")


async def fake_button(text, callback_data, **kwargs):
    return InlineKeyboardButton(text=text, callback_data=callback_data)


def test_unlimited_traffic_in_both_mode_offers_only_time(monkeypatch):
    monkeypatch.setattr(panels.db, "get_admin_by_id", AsyncMock(return_value=panel(unlimited=True)))
    monkeypatch.setattr(panels, "_admin_panel_identity", AsyncMock(return_value=("OpenVPN", None)))
    monkeypatch.setattr(panels, "policy_for_admin",
                        AsyncMock(return_value=(3, policy.RenewalPolicy(mode="both"))))
    monkeypatch.setattr(panels, "rates_for_admin", AsyncMock(return_value={
        "per_gb_toman": 5000, "per_30days_toman": 125000, "per_user_toman": 1000,
    }))
    monkeypatch.setattr(panels, "_button", fake_button)
    cb = callback("admin_renew_panel_19")
    run(panels.canonical_renew_panel(cb, state()))
    buttons = [b.callback_data for row in cb.message.edit_text.call_args.kwargs[
        "reply_markup"].inline_keyboard for b in row]
    assert "admin_renew_time_19" in buttons
    assert "admin_renew_traffic_19" not in buttons
    assert "admin_combo_renew_19" not in buttons


def test_combined_purchase_adds_both_limits_without_usage_reset(monkeypatch):
    admin = panel()
    offer = SimpleNamespace(id=70, name="Promax 250GB|60d", price=490000,
                            traffic_limit_bytes=250*1024**3, time_limit_seconds=60*86400)
    monkeypatch.setattr(panels.db, "get_admin_by_id", AsyncMock(return_value=admin))
    monkeypatch.setattr(panels, "policy_for_admin",
                        AsyncMock(return_value=(3, policy.RenewalPolicy(mode="choose"))))
    monkeypatch.setattr(panels.product_catalog, "plans_for_category",
                        AsyncMock(return_value=[offer]))
    monkeypatch.setattr(panels.rebecca_api, "find_admin",
                        AsyncMock(return_value={"username":"seller","data_limit":100*1024**3,"expire":1800000000}))
    monkeypatch.setattr(panels.db, "add_order", AsyncMock(return_value=80))
    update = AsyncMock(return_value=True)
    monkeypatch.setattr(panels.db, "update_order", update)
    monkeypatch.setattr(panels.db, "get_order_by_id", AsyncMock(return_value={"id":80,"price_snapshot":490000}))
    monkeypatch.setattr(legacy, "_mirror_admin_order_created", AsyncMock())
    from handlers import operations
    monkeypatch.setattr(operations, "render_checkout_payment", AsyncMock())
    cb = callback("admin_combo_renew_plan_19_70")
    run(panels.canonical_combined_renew_plan(cb, state()))
    assert panels.db.add_order.await_args.kwargs["price_snapshot"] == 490000
    assert update.await_args.kwargs == {
        "order_type": "renew", "target_admin_id": 19, "apply_as_reset": 0,
        "delta_traffic_bytes": 250*1024**3, "delta_time_seconds": 60*86400,
        "delta_users": 0,
    }
    operations.render_checkout_payment.assert_awaited_once()
    cb.answer.assert_awaited_once()


def test_combined_purchase_rejects_unlimited_remote_before_order(monkeypatch):
    admin = panel()
    offer = SimpleNamespace(id=70, name="ProMax", price=450000,
                            traffic_limit_bytes=100*1024**3, time_limit_seconds=30*86400)
    monkeypatch.setattr(panels.db, "get_admin_by_id", AsyncMock(return_value=admin))
    monkeypatch.setattr(panels, "policy_for_admin",
                        AsyncMock(return_value=(3, policy.RenewalPolicy(mode="both"))))
    monkeypatch.setattr(panels.product_catalog, "plans_for_category",
                        AsyncMock(return_value=[offer]))
    monkeypatch.setattr(panels.rebecca_api, "find_admin",
                        AsyncMock(return_value={"username":"seller","data_limit":None,"expire":1800000000}))
    create = AsyncMock()
    monkeypatch.setattr(panels.db, "add_order", create)
    cb = callback("admin_combo_renew_plan_19_70")
    run(panels.canonical_combined_renew_plan(cb, state()))
    create.assert_not_awaited()
    assert cb.answer.await_args.kwargs["show_alert"] is True


def test_old_amount_callback_cannot_bypass_configured_mode(monkeypatch):
    monkeypatch.setattr(legacy, "policy_for_admin",
                        AsyncMock(return_value=(3, policy.RenewalPolicy(mode="time"))))
    monkeypatch.setattr(legacy, "rates_for_admin", AsyncMock(return_value={
        "per_gb_toman": 2000, "per_30days_toman": 100000, "per_user_toman": 2000
    }))
    cb = callback("admin_renew_traffic_amount_19_100")
    assert run(legacy._renewal_rates_if_allowed(cb, panel(), "traffic")) is None
    cb.answer.assert_awaited_once()
    cb2 = callback("admin_renew_time_amount_19_30")
    assert run(legacy._renewal_rates_if_allowed(cb2, panel(), "time"))["per_30days_toman"] == 100000
