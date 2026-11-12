"""One identity checkpoint before every legacy or modern bank-card payment route."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from handlers import admin_handlers, operations, public_handlers
import purchase_kyc_flow


def run(coro):
    return asyncio.run(coro)


def callback(data, user_id=42):
    return SimpleNamespace(
        data=data,
        from_user=SimpleNamespace(id=user_id),
        message=SimpleNamespace(edit_text=AsyncMock(), answer=AsyncMock()),
        answer=AsyncMock(),
        bot=object(),
    )


def state():
    return SimpleNamespace(
        get_data=AsyncMock(return_value={}),
        update_data=AsyncMock(),
        set_state=AsyncMock(),
        clear=AsyncMock(),
    )


def test_legacy_reseller_order_delegates_to_canonical_checkout(monkeypatch):
    checkout = AsyncMock()
    monkeypatch.setattr(operations, "_checkout_prompt", checkout)
    db_add = AsyncMock()
    monkeypatch.setattr(admin_handlers.db, "add_order", db_add)
    cb, st = callback("admin_order_5"), state()

    run(admin_handlers.admin_order(cb, st))

    checkout.assert_awaited_once_with(cb, "a", 5, st)
    db_add.assert_not_awaited()


@pytest.mark.parametrize("module,prefix,source", [
    (admin_handlers, "admin_mark_paid_", "a"),
    (public_handlers, "public_mark_paid_", "p"),
])
def test_stale_paid_buttons_cannot_bypass_kyc(monkeypatch, module, prefix, source):
    cb = callback(prefix + "91")
    st = state()
    monkeypatch.setattr(module.db, "get_order_by_id", AsyncMock(return_value={
        "id": 91, "status": "pending", "user_id": 42,
    }))
    gate = AsyncMock(return_value=True)
    monkeypatch.setattr(purchase_kyc_flow, "require_checkout_kyc", gate)

    run(module.admin_mark_paid(cb, st) if source == "a"
        else module.public_mark_paid(cb, st))

    gate.assert_awaited_once()
    assert gate.await_args.kwargs["reference_id"] == 91
    assert gate.await_args.kwargs["telegram_user_id"] == 42
    st.set_state.assert_not_awaited()
    st.update_data.assert_not_awaited()


def test_renew_exemption_cannot_reveal_bank_destination(monkeypatch):
    gate = AsyncMock(return_value=True)
    cards = AsyncMock()
    old_renew_policy = AsyncMock(return_value=False)
    monkeypatch.setattr(purchase_kyc_flow, "require_checkout_kyc", gate)
    monkeypatch.setattr(operations.db, "get_cards", cards)
    monkeypatch.setattr(operations.order_kyc_gate, "requires_kyc", old_renew_policy)

    run(operations.render_checkout_payment(
        callback("unused").message,
        {
            "id": 91,
            "user_id": 42,
            "status": "pending",
            "order_type": "renew",
            "target_admin_id": 12,
            "plan_name_snapshot": "Renew",
            "price_snapshot": 10000,
        }, source="a",
    ))
    gate.assert_awaited_once()
    cards.assert_not_awaited()
    old_renew_policy.assert_not_awaited()


@pytest.mark.parametrize("name,data", [
    ("admin_full_renew_plan_selected", "admin_full_renew_plan_12_7"),
    ("admin_renew_traffic_amount", "admin_renew_traffic_amount_12_10"),
    ("admin_renew_time_amount", "admin_renew_time_amount_12_30"),
    ("admin_renew_users_amount", "admin_renew_users_amount_12_10"),
])
def test_all_reseller_renewals_use_single_payment_guard(monkeypatch, name, data):
    cb, st = callback(data), state()
    order = {"id": 91, "status": "pending", "user_id": 42, "order_type": "renew"}
    monkeypatch.setattr(admin_handlers.db, "get_admin_by_id", AsyncMock(return_value=SimpleNamespace(
        id=12, user_id=42, is_active=True,
    )))
    monkeypatch.setattr(admin_handlers.db, "get_plan_by_id", AsyncMock(return_value=SimpleNamespace(
        id=7, price=22000, name="Pro",
    )))
    monkeypatch.setattr(admin_handlers.db, "get_billing_rates", AsyncMock(return_value={
        "per_gb_toman": 100, "per_30days_toman": 1000, "per_user_toman": 100,
    }))
    add = AsyncMock(return_value=91)
    monkeypatch.setattr(admin_handlers.db, "add_order", add)
    monkeypatch.setattr(admin_handlers.db, "update_order", AsyncMock(return_value=True))
    monkeypatch.setattr(admin_handlers.db, "get_order_by_id", AsyncMock(return_value=order))
    monkeypatch.setattr(admin_handlers, "_mirror_admin_order_created", AsyncMock())
    renderer = AsyncMock()
    monkeypatch.setattr(operations, "render_checkout_payment", renderer)
    fn = getattr(admin_handlers, name)

    run(fn(cb) if name == "admin_full_renew_plan_selected" else fn(cb, st))

    add.assert_awaited_once()
    renderer.assert_awaited_once_with(cb.message, order, source="a")
    cb.answer.assert_awaited_once()


@pytest.mark.parametrize("name,data", [
    ("admin_renew_traffic_amount", "admin_renew_traffic_amount_12_10"),
    ("admin_renew_time_amount", "admin_renew_time_amount_12_30"),
    ("admin_renew_users_amount", "admin_renew_users_amount_12_10"),
])
def test_forged_renewal_rejects_wrong_numeric_telegram_owner(monkeypatch, name, data):
    cb, st = callback(data), state()
    monkeypatch.setattr(admin_handlers.db, "get_admin_by_id", AsyncMock(
        return_value=SimpleNamespace(id=12, user_id=999)
    ))
    db_add = AsyncMock()
    monkeypatch.setattr(admin_handlers.db, "add_order", db_add)

    run(getattr(admin_handlers, name)(cb, st))

    db_add.assert_not_awaited()
    cb.answer.assert_awaited_once()
    assert cb.answer.await_args.kwargs["show_alert"] is True


@pytest.mark.parametrize("module,handler_name", [
    (admin_handlers, "admin_receive_payment_receipt"),
    (public_handlers, "public_receive_payment_receipt"),
])
def test_old_receipt_fsm_cannot_submit_before_kyc(monkeypatch, module, handler_name):
    msg = SimpleNamespace(
        from_user=SimpleNamespace(id=42),
        photo=[SimpleNamespace(file_id="photo")],
        answer=AsyncMock(),
    )
    st = state()
    st.get_data.return_value = {"order_id": 91}
    gate = AsyncMock(return_value=True)
    monkeypatch.setattr(purchase_kyc_flow, "require_checkout_kyc", gate)
    db_submit = AsyncMock()
    monkeypatch.setattr(module.db, "submit_order_receipt", db_submit)

    run(getattr(module, handler_name)(msg, st))

    gate.assert_awaited_once()
    assert gate.await_args.kwargs["edit"] is False
    st.clear.assert_awaited_once()
    db_submit.assert_not_awaited()


def test_requested_username_purchase_uses_guarded_payment_screen(monkeypatch):
    from handlers import trial_experience
    cb_msg = SimpleNamespace(
        from_user=SimpleNamespace(id=42),
        answer=AsyncMock(),
        bot=object(),
    )
    order = {
        "id": 91, "status": "pending", "user_id": 42,
        "plan_name_snapshot": "Pro", "price_snapshot": 20000,
    }
    monkeypatch.setattr(trial_experience.db, "get_plan_by_id", AsyncMock(return_value=SimpleNamespace(
        id=6, name="Pro", price=20000, is_active=True,
    )))
    monkeypatch.setattr(
        trial_experience.trial_experience_service,
        "validate_username",
        lambda value: value,
    )
    monkeypatch.setattr(
        trial_experience.trial_experience_service,
        "username_available", AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        trial_experience.trial_experience_service,
        "save_order_username", AsyncMock(),
    )
    monkeypatch.setattr(trial_experience.db, "add_order", AsyncMock(return_value=91))
    monkeypatch.setattr(trial_experience.db, "get_order_by_id", AsyncMock(return_value=order))
    cards = AsyncMock()
    monkeypatch.setattr(trial_experience.db, "get_cards", cards)
    renderer = AsyncMock()
    monkeypatch.setattr(operations, "render_checkout_payment", renderer)

    run(trial_experience._create_order_and_render(
        cb_msg, source="p", plan_id=6,
        username="my-panel-name", quote=None,
    ))

    renderer.assert_awaited_once()
    wrapped, same_order = renderer.await_args.args
    assert same_order["id"] == order["id"]
    assert same_order["user_id"] == 42
    assert same_order["status"] == "pending"
    assert same_order["price_snapshot"] == 20000
    assert renderer.await_args.kwargs["source"] == "p"
    assert wrapped._message is cb_msg
    cards.assert_not_awaited()
