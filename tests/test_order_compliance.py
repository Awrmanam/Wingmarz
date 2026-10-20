import asyncio
import aiosqlite

import pytest

from order_compliance import (
    KYC_APPROVED,
    KYC_PENDING,
    KYC_SUBMITTED,
    KYC_REJECTED,
    PAYMENT_APPROVED,
    PAYMENT_RECEIPT,
    REFUND_PENDING,
    OrderComplianceService,
)


def run(coro):
    return asyncio.run(coro)


def test_paid_order_waits_for_kyc_then_becomes_ready(tmp_path):
    service = OrderComplianceService(str(tmp_path / "orders.db"))

    async def scenario():
        gate = await service.ensure_order(11, 55, kyc_required=True)
        assert gate.ready_to_issue is False

        assert await service.mark_receipt_submitted(11, 55) is True
        gate = await service.get(11)
        assert gate.payment_state == PAYMENT_RECEIPT
        assert gate.kyc_state == KYC_PENDING

        gate = await service.approve_payment(11, 999, identity_approved=False)
        assert gate.payment_state == PAYMENT_APPROVED
        assert gate.ready_to_issue is False

        ready = await service.approve_user_kyc(55)
        assert ready == [11]
        gate = await service.get(11)
        assert gate.kyc_state == KYC_APPROVED
        assert gate.ready_to_issue is True

    run(scenario())


def test_existing_trusted_user_skips_kyc_gate(tmp_path):
    service = OrderComplianceService(str(tmp_path / "orders.db"))

    async def scenario():
        gate = await service.ensure_order(12, 56, kyc_required=False)
        await service.mark_receipt_submitted(12, 56)
        gate = await service.approve_payment(12, 999, identity_approved=True)
        assert gate.ready_to_issue is True

    run(scenario())


def test_rejected_kyc_after_payment_enters_refund_queue(tmp_path):
    service = OrderComplianceService(str(tmp_path / "orders.db"))

    async def scenario():
        await service.ensure_order(13, 57, kyc_required=True)
        await service.mark_receipt_submitted(13, 57)
        await service.approve_payment(13, 999, identity_approved=False)

        refunds = await service.reject_user_kyc(57)
        assert refunds == [13]

        gate = await service.get(13)
        assert gate.kyc_state == KYC_REJECTED
        assert gate.refund_state == REFUND_PENDING
        assert gate.ready_to_issue is False

        gate = await service.mark_refunded(13)
        assert gate.refund_state == "refunded"

    run(scenario())


def test_rejected_receipt_can_be_resubmitted(tmp_path):
    service = OrderComplianceService(str(tmp_path / "orders.db"))

    async def scenario():
        await service.ensure_order(14, 58, kyc_required=True)
        await service.mark_receipt_submitted(14, 58)
        gate = await service.reject_receipt(14, 999)
        assert gate.payment_state == "rejected"
        assert await service.reset_for_new_receipt(14) is True
        assert await service.mark_receipt_submitted(14, 58) is True

    run(scenario())



def test_paid_pending_kyc_expires_to_refund_queue(tmp_path):
    path = str(tmp_path / "orders.db")
    service = OrderComplianceService(path)

    async def scenario():
        await service.ensure_order(15, 59, kyc_required=True)
        await service.mark_receipt_submitted(15, 59)
        await service.approve_payment(15, 999, identity_approved=False)

        async with aiosqlite.connect(path) as conn:
            await conn.execute(
                "UPDATE order_compliance SET payment_reviewed_at=datetime('now','-49 hours') WHERE order_id=15"
            )
            await conn.commit()

        expired = await service.expire_overdue_kyc(48)
        assert expired == [{"order_id": 15, "user_id": 59}]
        gate = await service.get(15)
        assert gate.refund_state == REFUND_PENDING
        assert gate.ready_to_issue is False

        # Idempotent: it must not enqueue the same refund twice.
        assert await service.expire_overdue_kyc(48) == []

    run(scenario())



def test_submitted_kyc_does_not_timeout_while_waiting_for_review(tmp_path):
    path = str(tmp_path / "orders.db")
    service = OrderComplianceService(path)

    async def scenario():
        await service.ensure_order(16, 60, kyc_required=True)
        await service.mark_receipt_submitted(16, 60)
        await service.approve_payment(16, 999, identity_approved=False)
        await service.mark_user_kyc_submitted(60)

        async with aiosqlite.connect(path) as conn:
            await conn.execute(
                "UPDATE order_compliance SET payment_reviewed_at=datetime('now','-72 hours') WHERE order_id=16"
            )
            await conn.commit()

        gate = await service.get(16)
        assert gate.kyc_state == KYC_SUBMITTED
        assert await service.expire_overdue_kyc(48) == []
        gate = await service.get(16)
        assert gate.refund_state == "none"

    run(scenario())
