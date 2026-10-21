import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.types import Chat, Message, User

import group_ops_start_middleware as start_module
from group_ops_service import GroupOpsService, TOPICS
from order_compliance_service import OrderComplianceService


def run(coro):
    return asyncio.run(coro)


class FakeForumBot:
    def __init__(self):
        self.created = []
        self.messages = []
        self.photos = []
        self.next_thread = 100

    async def create_forum_topic(self, chat_id, name):
        self.next_thread += 1
        self.created.append((chat_id, name, self.next_thread))
        return SimpleNamespace(message_thread_id=self.next_thread)

    async def send_message(self, **kwargs):
        self.messages.append(kwargs)
        return SimpleNamespace(message_id=len(self.messages))

    async def send_photo(self, **kwargs):
        self.photos.append(kwargs)
        return SimpleNamespace(message_id=len(self.photos))


def test_receipts_is_a_first_class_group_topic():
    assert TOPICS["receipts"] == "🧾 رسیدهای پرداخت"


def test_group_ops_auto_creates_missing_topic(tmp_path):
    service = GroupOpsService(str(tmp_path / "ops.db"))
    bot = FakeForumBot()

    async def scenario():
        await service.configure_group(-100123, 99)
        assert await service.send_text(bot, "receipts", "receipt event") is True
        topics = await service.topics()
        assert "receipts" in topics
        assert bot.created[0][1] == "🧾 رسیدهای پرداخت"
        assert bot.messages[-1]["message_thread_id"] == topics["receipts"]["thread_id"]

    run(scenario())


def test_group_ops_repairs_stale_thread_and_retries(tmp_path):
    service = GroupOpsService(str(tmp_path / "ops.db"))

    class RepairBot(FakeForumBot):
        async def send_message(self, **kwargs):
            if kwargs.get("message_thread_id") == 999:
                raise RuntimeError("message thread not found")
            return await super().send_message(**kwargs)

    bot = RepairBot()

    async def scenario():
        await service.configure_group(-100123, 99)
        await service._store_topic("starts", TOPICS["starts"], 999)
        assert await service.send_text(bot, "starts", "hello") is True
        topics = await service.topics()
        assert topics["starts"]["thread_id"] != 999
        assert bot.created
        assert bot.messages[-1]["text"] == "hello"

    run(scenario())


def test_order_compliance_payment_then_kyc_lifecycle(tmp_path):
    service = OrderComplianceService(str(tmp_path / "orders.db"))

    async def scenario():
        await service.note_receipt(10, 55, kyc_required=True)
        row = await service.get(10)
        assert row["payment_confirmed"] == 0
        assert row["kyc_state"] == "awaiting"
        assert await service.has_kyc_case(55) is True

        await service.confirm_payment(10, 55, 999, kyc_required=True)
        row = await service.get(10)
        assert row["payment_confirmed"] == 1
        assert row["kyc_state"] == "awaiting"

        ready = await service.mark_kyc_approved(55)
        assert ready == [10]
        assert await service.ready_for_issue(10) is True

    run(scenario())


def test_rejected_paid_kyc_enters_refund_queue_and_resubmit_reopens(tmp_path):
    service = OrderComplianceService(str(tmp_path / "orders.db"))

    async def scenario():
        await service.note_receipt(20, 77, kyc_required=True)
        await service.confirm_payment(20, 77, 999, kyc_required=True)
        refund_ids = await service.mark_kyc_rejected(77)
        assert refund_ids == [20]
        row = await service.get(20)
        assert row["kyc_state"] == "rejected"
        assert row["refund_state"] == "pending"

        await service.reset_kyc_pending(77)
        row = await service.get(20)
        assert row["kyc_state"] == "awaiting"
        assert row["refund_state"] == "none"

    run(scenario())


def test_start_middleware_logs_before_route_handler(monkeypatch):
    middleware = start_module.GroupOpsStartMiddleware()
    user = User(id=12345, is_bot=False, first_name="Buyer", username="buyer")
    message = Message(
        message_id=1,
        date=datetime.now(timezone.utc),
        chat=Chat(id=12345, type="private"),
        from_user=user,
        text="/start",
    )

    monkeypatch.setattr(start_module.config, "SUDO_ADMINS", [])
    monkeypatch.setattr(start_module.db, "get_admins_for_user", AsyncMock(return_value=[]))
    monkeypatch.setattr(start_module.kyc_service, "get_profile", AsyncMock(return_value=None))
    monkeypatch.setattr(start_module.trust_service, "is_manually_trusted", AsyncMock(return_value=False))
    notify = AsyncMock(return_value=True)
    monkeypatch.setattr(start_module.group_ops_service, "notify_start", notify)

    called = []

    async def handler(event, data):
        called.append(True)
        return "ok"

    result = run(middleware(handler, message, {"bot": SimpleNamespace()}))
    assert result == "ok"
    assert called == [True]
    notify.assert_awaited_once()
    assert notify.await_args.kwargs["account_kind"] == "مشتری"


def test_receipt_handlers_use_dedicated_receipts_topic():
    for path in (
        "handlers/public_handlers.py",
        "handlers/admin_handlers.py",
        "handlers/payg.py",
        "handlers/payg_offers.py",
    ):
        source = open(path, encoding="utf-8").read()
        assert '"receipts"' in source
