import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.types import Chat, Message, User

import group_ops_middleware as middleware_module
from group_ops_middleware import GroupOpsErrorMiddleware
from group_ops_service import GroupOpsService, TOPICS


def run(coro):
    return asyncio.run(coro)


def test_receipts_have_dedicated_group_topic():
    assert TOPICS["receipts"] == "🧾 رسیدها"
    assert TOPICS["receipts"] != TOPICS["payments"]
    assert TOPICS["kyc"] == "🪪 احراز هویت"


def test_missing_topic_is_created_on_first_delivery(tmp_path):
    service = GroupOpsService(str(tmp_path / "ops.db"))
    bot = SimpleNamespace(
        create_forum_topic=AsyncMock(return_value=SimpleNamespace(message_thread_id=77)),
        send_message=AsyncMock(return_value=SimpleNamespace(message_id=1)),
    )

    async def scenario():
        await service.configure_group(-100123, 999)
        assert await service.send_text(bot, "receipts", "receipt") is True
        topics = await service.topics()
        assert topics["receipts"]["thread_id"] == 77
        status = await service.delivery_status()
        assert status["receipts"]["ok"] == 1

    run(scenario())
    bot.create_forum_topic.assert_awaited_once_with(chat_id=-100123, name="🧾 رسیدها")
    assert bot.send_message.await_args.kwargs["message_thread_id"] == 77


def test_stale_topic_self_repairs_and_retries(tmp_path):
    service = GroupOpsService(str(tmp_path / "ops.db"))
    bot = SimpleNamespace(
        create_forum_topic=AsyncMock(return_value=SimpleNamespace(message_thread_id=88)),
        send_message=AsyncMock(side_effect=[RuntimeError("message thread not found"), SimpleNamespace(message_id=2)]),
    )

    async def scenario():
        await service.configure_group(-100456, 999)
        async with __import__("aiosqlite").connect(service.db_path) as conn:
            await conn.execute(
                "INSERT INTO group_ops_topics(topic_key,title,thread_id) VALUES(?,?,?)",
                ("starts", "Starts", 10),
            )
            await conn.commit()
        assert await service.send_text(bot, "starts", "hello") is True
        topics = await service.topics()
        assert topics["starts"]["thread_id"] == 88

    run(scenario())
    assert bot.send_message.await_count == 2
    bot.create_forum_topic.assert_awaited_once_with(chat_id=-100456, name="👋 استارت‌ها")


def test_global_middleware_logs_public_start_even_without_admin_record(monkeypatch):
    mw = GroupOpsErrorMiddleware()
    user = User(id=555, is_bot=False, first_name="New", username="new_user")
    chat = Chat(id=555, type="private")
    message = Message(
        message_id=42,
        date=datetime.now(timezone.utc),
        chat=chat,
        from_user=user,
        text="/start",
    )
    bot = SimpleNamespace()

    handler = AsyncMock(return_value="handled")
    monkeypatch.setattr(middleware_module.db, "get_admins_for_user", AsyncMock(return_value=[]))
    monkeypatch.setattr(middleware_module.kyc_service, "get_profile", AsyncMock(return_value=None))
    monkeypatch.setattr(middleware_module.trust_service, "is_manually_trusted", AsyncMock(return_value=False))
    monkeypatch.setattr(middleware_module.group_ops_service, "claim_event", AsyncMock(return_value=True))
    notify = AsyncMock(return_value=True)
    monkeypatch.setattr(middleware_module.group_ops_service, "notify_start", notify)

    result = run(mw(handler, message, {"bot": bot}))

    assert result == "handled"
    notify.assert_awaited_once()
    kwargs = notify.await_args.kwargs
    assert kwargs["account_kind"] == "مشتری"
    assert kwargs["kyc_status"] == "not_submitted"
    assert kwargs["trusted"] is False
