"""Explicit add-quota/restart-expiry semantics and safe order snapshots."""
import asyncio
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from database import Database
import renewal_policy as policy
from handlers import panel_experience as panels
from handlers import admin_handlers as legacy
from handlers import operations
import rebecca_renewal as renewal
from rebecca_renewal_journal import RebeccaRenewalJournal


def run(coro):
    return asyncio.run(coro)


def cb(data, *, user=42):
    return SimpleNamespace(
        data=data,
        from_user=SimpleNamespace(id=user),
        message=SimpleNamespace(edit_text=AsyncMock()),
        answer=AsyncMock(),
        bot=SimpleNamespace(),
    )


def state():
    return SimpleNamespace(clear=AsyncMock(), update_data=AsyncMock())


def admin(*, unlimited=False):
    return SimpleNamespace(
        id=19, user_id=42, origin_plan_id=7, admin_name="seller",
        marzban_username="seller", is_active=True,
        max_total_traffic=0 if unlimited else 100 * 1024**3,
        max_total_time=30 * 86400, allow_incremental_renewal=None,
    )


def product():
    return SimpleNamespace(
        id=70, name="Promax 250GB|60d", price=490000,
        traffic_limit_bytes=250 * 1024**3, time_limit_seconds=60 * 86400,
    )


def configure(monkeypatch, mode="reset_time"):
    monkeypatch.setattr(panels.db, "get_admin_by_id", AsyncMock(return_value=admin()))
    monkeypatch.setattr(
        panels, "policy_for_admin",
        AsyncMock(return_value=(3, policy.RenewalPolicy(mode=mode))),
    )
    monkeypatch.setattr(
        panels.product_catalog, "plans_for_category",
        AsyncMock(return_value=[product()]),
    )
    monkeypatch.setattr(
        panels.rebecca_api, "find_admin",
        AsyncMock(return_value={
            "username": "seller", "data_limit": 100 * 1024**3,
            "expire": 1800000000,
        }),
    )
    monkeypatch.setattr(panels.db, "add_order", AsyncMock(return_value=80))
    monkeypatch.setattr(panels.db, "update_order", AsyncMock(return_value=True))
    monkeypatch.setattr(
        panels.db, "get_order_by_id",
        AsyncMock(return_value={"id": 80, "price_snapshot": 490000}),
    )
    monkeypatch.setattr(legacy, "_mirror_admin_order_created", AsyncMock())
    monkeypatch.setattr(operations, "render_checkout_payment", AsyncMock())


def test_new_mode_is_independent_of_old_combined_mode():
    assert policy.allows("reset_time", "reset_time")
    assert not policy.allows("reset_time", "both")
    assert not policy.allows("both", "reset_time")
    assert policy.allows("choose", "reset_time")
    assert not policy.allows("legacy", "reset_time")
    assert "ریست" not in policy.MODES["both"]
    assert "شروع دوباره" in policy.MODES["reset_time"]


def test_renewal_mode_persists_and_old_category_modes_stay_unchanged(tmp_path, monkeypatch):
    local_db = Database(str(tmp_path / "test.sqlite"))
    run(local_db.init_db())
    monkeypatch.setattr(policy, "db", local_db)
    assert run(policy.get_policy(7)).mode == "legacy"
    run(policy.save_mode(7, "reset_time"))
    run(policy.save_mode(8, "both"))
    assert run(policy.get_policy(7)).mode == "reset_time"
    assert run(policy.get_policy(8)).mode == "both"


def test_menu_shows_reset_time_distinct_from_add_time(monkeypatch):
    configure(monkeypatch, mode="choose")
    monkeypatch.setattr(panels, "_admin_panel_identity", AsyncMock(return_value=("Promax", None)))
    monkeypatch.setattr(
        panels, "rates_for_admin",
        AsyncMock(return_value={
            "per_gb_toman": 1000, "per_30days_toman": 90000,
            "per_user_toman": 2000,
        }),
    )
    c = cb("admin_renew_panel_19")
    run(panels.canonical_renew_panel(c, state()))
    callback_ids = [
        b.callback_data for row in c.message.edit_text.call_args.kwargs[
            "reply_markup"
        ].inline_keyboard for b in row
    ]
    assert "admin_reset_renew_19" in callback_ids
    assert "admin_combo_renew_19" in callback_ids
    assert "admin_renew_traffic_19" in callback_ids
    assert "admin_renew_time_19" in callback_ids


def test_reset_time_purchase_saves_explicit_snapshot(monkeypatch):
    configure(monkeypatch)
    c = cb("admin_reset_renew_plan_19_70")
    run(panels.canonical_reset_time_renew_plan(c, state()))
    assert panels.db.add_order.await_args.kwargs["price_snapshot"] == 490000
    fields = panels.db.update_order.await_args.kwargs
    assert fields == {
        "order_type": "renew", "target_admin_id": 19,
        "apply_as_reset": 0, "delta_traffic_bytes": 250*1024**3,
        "delta_time_seconds": 60*86400, "delta_users": 0,
        "renewal_time_reset": 1,
    }
    operations.render_checkout_payment.assert_awaited_once()


def test_stale_button_rejected_if_reset_mode_disabled(monkeypatch):
    configure(monkeypatch, mode="both")
    c = cb("admin_reset_renew_plan_19_70")
    run(panels.canonical_reset_time_renew_plan(c, state()))
    panels.db.add_order.assert_not_awaited()
    assert c.answer.await_args.kwargs.get("show_alert") is True


def test_old_sqlite_orders_table_migrates_time_reset_column(tmp_path):
    dbfile = tmp_path / "legacy.sqlite"
    with sqlite3.connect(dbfile) as conn:
        conn.execute("""CREATE TABLE orders(
            id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL,
            plan_id INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'pending'
        )""")
    run(Database(str(dbfile)).init_db())
    with sqlite3.connect(dbfile) as conn:
        columns = [r[1] for r in conn.execute("PRAGMA table_info(orders)")]
        assert "renewal_time_reset" in columns
        conn.execute(
            "INSERT INTO orders(user_id, plan_id, status) VALUES(42, 2, 'pending')"
        )
        assert conn.execute(
            "SELECT renewal_time_reset FROM orders ORDER BY id DESC LIMIT 1"
        ).fetchone()[0] == 0


def test_reset_time_rebecca_adds_quota_and_resets_expiry_without_usage_reset(tmp_path, monkeypatch):
    from tests.test_rebecca_renewal_journal import fixture
    # Existing test harness already exercises durable order reservation.
    path, db, admin_id, journal, api, remote, order = fixture(
        monkeypatch, tmp_path
    )
    original_expire = remote["expire"]
    order.update({
        "order_type": "renew", "renewal_time_reset": 1,
        "delta_traffic_bytes": 50, "delta_time_seconds": 60 * 86400,
        "delta_users": 0,
    })
    # Use a stable approval timestamp, strictly BEFORE original expiration.
    monkeypatch.setattr(renewal.time, "time", lambda: 1760000000)
    result = run(renewal.apply_rebecca_renewal(order, 99))
    assert remote["data_limit"] == 150
    assert remote["expire"] == 1760000000 + 60 * 86400
    assert remote["expire"] != original_expire + 60 * 86400
    api.reset_admin_usage.assert_not_awaited()
    api.update_admin_fields.assert_awaited_once()
    assert "انقضای جدید" in result.message
    with pytest.raises(renewal.RebeccaRenewalError):
        run(renewal.apply_rebecca_renewal(order, 99))
    assert api.update_admin_fields.await_count == 1


def test_invalid_reset_flag_with_old_full_reset_is_rejected_without_api_update(tmp_path, monkeypatch):
    from tests.test_rebecca_renewal_journal import fixture
    _, _, _, _, api, _, order = fixture(
        monkeypatch, tmp_path, reset=True
    )
    order.update({
        "order_type": "renew", "renewal_time_reset": 1,
        "delta_traffic_bytes": 50, "delta_time_seconds": 60 * 86400,
    })
    with pytest.raises(renewal.RebeccaRenewalError, match="نامعتبر"):
        run(renewal.apply_rebecca_renewal(order, 99))
    api.update_admin_fields.assert_not_awaited()
    api.reset_admin_usage.assert_not_awaited()
