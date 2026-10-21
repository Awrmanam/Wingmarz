import asyncio

from database import Database


def run(coro):
    return asyncio.run(coro)


def test_paid_order_waits_for_kyc_then_releases(tmp_path):
    db = Database(str(tmp_path / "orders.db"))

    async def scenario():
        await db.init_db()
        order_id = await db.add_order(55, 1, 250000, "Gold")
        assert order_id
        assert await db.submit_order_receipt(order_id, 55, "receipt-file")
        assert await db.mark_order_paid_awaiting_kyc(order_id, 999)
        held = await db.get_paid_awaiting_kyc_orders(55)
        assert [item["id"] for item in held] == [order_id]
        released = await db.release_paid_kyc_orders(55)
        assert released == [order_id]
        assert (await db.get_order_by_id(order_id))["status"] == "submitted"

    run(scenario())


def test_kyc_rejection_creates_idempotent_refund_request(tmp_path):
    db = Database(str(tmp_path / "refund.db"))

    async def scenario():
        await db.init_db()
        order_id = await db.add_order(77, 2, 410000, "Pro")
        assert order_id
        await db.submit_order_receipt(order_id, 77, "receipt-file")
        await db.mark_order_paid_awaiting_kyc(order_id, 999)

        first = await db.mark_kyc_orders_refund_pending(77)
        second = await db.mark_kyc_orders_refund_pending(77)
        assert first == [order_id]
        assert second == []

        order = await db.get_order_by_id(order_id)
        assert order["status"] == "refund_pending"
        refunds = await db.get_refund_requests()
        assert len(refunds) == 1
        assert refunds[0]["reference_type"] == "order"
        assert refunds[0]["reference_id"] == order_id
        assert refunds[0]["amount_toman"] == 410000

    run(scenario())
