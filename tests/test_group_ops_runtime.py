import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiosqlite
from aiogram.types import Chat, Message, User

import config
import group_ops_events
import group_ops_middleware
from group_ops_service import GroupOpsService
import handlers.sudo_handlers as sudo
import order_kyc_gate as gate_module


def run(coro):
    return asyncio.run(coro)


class FakeTopicBot:
    def __init__(self):
        self.created = []
        self.messages = []
        self.photos = []

    async def create_forum_topic(self, chat_id, name):
        self.created.append((chat_id, name))
        return SimpleNamespace(message_thread_id=100 + len(self.created))

    async def send_message(self, **kwargs):
        self.messages.append(kwargs)
        return SimpleNamespace(message_id=len(self.messages))

    async def send_photo(self, **kwargs):
        self.photos.append(kwargs)
        return SimpleNamespace(message_id=len(self.photos))


def test_group_ops_auto_creates_missing_receipts_topic(tmp_path):
    service = GroupOpsService(str(tmp_path / "ops.db"))
    bot = FakeTopicBot()

    async def scenario():
        await service.configure_group(-100555, 1)
        ok = await service.send_photo(
            bot,
            "receipts",
            "telegram-file-id",
            caption="receipt",
        )
        assert ok is True
        assert bot.created == [(-100555, "🧾 رسیدهای پرداخت")]
        assert bot.photos[0]["message_thread_id"] == 101
        topics = await service.topics()
        assert topics["receipts"]["thread_id"] == 101

    run(scenario())


def test_global_start_middleware_logs_public_start(monkeypatch):
    middleware = group_ops_middleware.GroupOpsStartMiddleware()
    notify = AsyncMock(return_value=True)
    monkeypatch.setattr(
        group_ops_middleware.group_ops_service,
        "get_config",
        AsyncMock(return_value={"enabled": 1, "chat_id": -1001}),
    )
    monkeypatch.setattr(group_ops_middleware.group_ops_service, "notify_start", notify)
    monkeypatch.setattr(group_ops_middleware.db, "get_admins_for_user", AsyncMock(return_value=[]))
    monkeypatch.setattr(group_ops_middleware.kyc_service, "status", AsyncMock(return_value="not_submitted"))
    monkeypatch.setattr(
        group_ops_middleware.trust_service,
        "is_manually_trusted",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(config, "SUDO_ADMINS", [999])

    event = Message(
        message_id=77,
        date=datetime.now(timezone.utc),
        chat=Chat(id=42, type="private"),
        from_user=User(id=42, is_bot=False, first_name="Buyer", username="buyer42"),
        text="/start",
    )
    handler = AsyncMock(return_value="ok")
    bot = SimpleNamespace()

    result = run(middleware(handler, event, {"bot": bot}))

    assert result == "ok"
    notify.assert_awaited_once()
    kwargs = notify.await_args.kwargs
    assert kwargs["account_kind"] == "مشتری"
    assert kwargs["kyc_status"] == "not_submitted"


def test_order_receipt_always_goes_to_receipts_topic(monkeypatch):
    send_photo = AsyncMock(return_value=True)
    send_text = AsyncMock(return_value=True)
    monkeypatch.setattr(group_ops_events.group_ops_service, "send_photo", send_photo)
    monkeypatch.setattr(group_ops_events.group_ops_service, "send_text", send_text)
    monkeypatch.setattr(
        group_ops_events.trust_service,
        "is_identity_approved",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(
        group_ops_events.trust_service,
        "auto_approve_enabled",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(
        group_ops_events.trust_service,
        "payment_verification",
        AsyncMock(return_value=None),
    )

    order = {
        "id": 12,
        "user_id": 42,
        "price_snapshot": 500000,
        "plan_name_snapshot": "Promax 100GB",
    }
    user = SimpleNamespace(id=42, username="buyer")

    run(group_ops_events.mirror_order_receipt(
        SimpleNamespace(),
        order=order,
        user=user,
        file_id="receipt-file",
    ))

    assert send_photo.await_args.args[1] == "receipts"
    topics = [call.args[1] for call in send_text.await_args_list]
    assert "payments" in topics
    assert "unverified" in topics


def test_paid_order_is_held_until_kyc(monkeypatch):
    db = SimpleNamespace(
        get_order_by_id=AsyncMock(return_value={
            "id": 1,
            "user_id": 42,
            "plan_id": 2,
            "status": "submitted",
            "order_type": "new",
            "plan_name_snapshot": "Gold",
            "price_snapshot": 100000,
        }),
        update_order=AsyncMock(return_value=True),
    )
    monkeypatch.setattr(config, "SUDO_ADMINS", [99])
    monkeypatch.setattr(sudo, "db", db)
    monkeypatch.setattr(sudo.order_kyc_gate, "requires_kyc", AsyncMock(return_value=True))
    monkeypatch.setattr(sudo.order_kyc_gate, "hold_after_payment", AsyncMock(return_value=True))
    monkeypatch.setattr(sudo.group_ops_service, "send_text", AsyncMock(return_value=True))

    class Msg:
        photo = [object()]
        caption = "receipt"
        edit_caption = AsyncMock()
        edit_text = AsyncMock()
        edit_reply_markup = AsyncMock()
        answer = AsyncMock()

    bot = SimpleNamespace(send_message=AsyncMock())
    callback = SimpleNamespace(
        data="order_approve_1",
        from_user=SimpleNamespace(id=99),
        bot=bot,
        message=Msg(),
        answer=AsyncMock(),
    )

    run(sudo.order_approve(callback))

    sudo.order_kyc_gate.hold_after_payment.assert_awaited_once()
    bot.send_message.assert_awaited_once()
    callback.message.edit_caption.assert_awaited_once()
    assert "منتظر KYC" in callback.message.edit_caption.await_args.kwargs["caption"]


def test_kyc_gate_release_returns_paid_orders_to_submitted(tmp_path, monkeypatch):
    path = str(tmp_path / "gate.db")
    monkeypatch.setattr(config, "DATABASE_PATH", path)
    service = gate_module.OrderKycGate()

    async def scenario():
        async with aiosqlite.connect(path) as conn:
            await conn.execute(
                """
                CREATE TABLE orders (
                    id INTEGER PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    status TEXT,
                    plan_name_snapshot TEXT,
                    price_snapshot INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            await conn.execute(
                "INSERT INTO orders(id,user_id,status,plan_name_snapshot,price_snapshot) VALUES(1,42,'paid_awaiting_kyc','Gold',100000)"
            )
            await conn.commit()

        await service.ensure_schema()
        async with aiosqlite.connect(path) as conn:
            await conn.execute(
                """
                INSERT INTO order_kyc_gate(order_id,user_id,state,payment_approved_by)
                VALUES(1,42,'awaiting_kyc',99)
                """
            )
            await conn.commit()

        monkeypatch.setattr(service.__class__, "db_path", property(lambda _self: path))
        monkeypatch.setattr(gate_module.group_ops_service, "audit", AsyncMock())

        rows = await service.release_after_kyc(42, 99)
        assert [row["id"] for row in rows] == [1]
        async with aiosqlite.connect(path) as conn:
            async with conn.execute("SELECT status FROM orders WHERE id=1") as cur:
                assert (await cur.fetchone())[0] == "submitted"

    run(scenario())


def test_canonical_checkout_warns_about_kyc_before_payment():
    source = open("handlers/operations.py", encoding="utf-8").read()
    start = source.index("async def _create_checkout_order")
    end = source.index("# ---------------------------- Trial configs", start)
    checkout = source[start:end]
    assert "بعد از تأیید پرداخت باید احراز هویت را تکمیل کنید" in checkout
    assert "order_kyc_gate.requires_kyc" in checkout
