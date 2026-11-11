"""Identity-safe /start telemetry: numeric Telegram ID, once only, persistent."""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiosqlite
import pytest
from aiogram.types import Chat, Message, User

import config
import group_ops_middleware
from group_ops_service import GroupOpsService


def run(coro):
    return asyncio.run(coro)


def make_start(user_id=42, username="original", text="/start", chat_type="private"):
    return Message(
        message_id=1,
        date=datetime.now(timezone.utc),
        chat=Chat(id=user_id, type=chat_type),
        from_user=User(id=user_id, is_bot=False, first_name="Customer", username=username),
        text=text,
    )


class FakeTopicBot:
    def __init__(self, fail_send=False):
        self.fail_send = fail_send
        self.messages = []
        self.topics = []

    async def create_forum_topic(self, chat_id, name):
        self.topics.append((chat_id, name))
        return SimpleNamespace(message_thread_id=900 + len(self.topics))

    async def send_message(self, **kwargs):
        self.messages.append(kwargs)
        if self.fail_send:
            raise RuntimeError("simulated Telegram outage")
        return SimpleNamespace(message_id=len(self.messages))


def setup(monkeypatch, tmp_path):
    service = GroupOpsService(str(tmp_path / "telegram-identities.db"))
    bot = FakeTopicBot()
    middleware = group_ops_middleware.GroupOpsStartMiddleware()
    monkeypatch.setattr(group_ops_middleware, "group_ops_service", service)
    monkeypatch.setattr(group_ops_middleware.db, "get_admins_for_user", AsyncMock(return_value=[]))
    monkeypatch.setattr(group_ops_middleware.kyc_service, "status", AsyncMock(return_value="not_submitted"))
    monkeypatch.setattr(
        group_ops_middleware.trust_service, "is_manually_trusted", AsyncMock(return_value=False)
    )
    monkeypatch.setattr(config, "SUDO_ADMINS", [999])
    return service, bot, middleware


def test_repeated_start_and_username_change_do_not_reemit(monkeypatch, tmp_path):
    service, bot, middleware = setup(monkeypatch, tmp_path)

    async def scenario():
        await service.configure_group(-100789, 999)
        handler = AsyncMock(return_value="handled")
        for name in ("original", "renamed", None, "renamed-again"):
            result = await middleware(
                handler, make_start(username=name), {"bot": bot}
            )
            assert result == "handled"
        assert handler.await_count == 4
        assert len(bot.messages) == 1
        assert len(bot.topics) == 1
        assert bot.messages[0]["chat_id"] == -100789
        assert bot.messages[0]["message_thread_id"] == 901
        assert "42" in bot.messages[0]["text"]
        stats = await service.start_statistics()
        assert stats == {
            "unique_telegram_ids": 1,
            "total_start_commands": 4,
            "notifications_sent": 1,
        }
        async with aiosqlite.connect(service.db_path) as conn:
            async with conn.execute(
                "SELECT telegram_user_id,start_count,delivery_status FROM group_ops_start_users"
            ) as cursor:
                rows = await cursor.fetchall()
        assert rows == [(42, 4, "sent")]

        # A fresh service instance sees the same database and must not resend
        # even after a process restart.
        monkeypatch.setattr(
            group_ops_middleware, "group_ops_service", GroupOpsService(service.db_path)
        )
        await group_ops_middleware.GroupOpsStartMiddleware()(
            handler, make_start(username="post-restart"), {"bot": bot}
        )
        assert len(bot.messages) == 1
        assert (await service.start_statistics())["total_start_commands"] == 5

        # A different numeric ID is a new identity even if it has the same username.
        await group_ops_middleware.GroupOpsStartMiddleware()(
            handler, make_start(user_id=43, username="post-restart"), {"bot": bot}
        )
        assert len(bot.messages) == 2
        assert (await service.start_statistics())["unique_telegram_ids"] == 2

    run(scenario())


def test_simultaneous_starts_are_atomic_by_numeric_telegram_id(tmp_path):
    service = GroupOpsService(str(tmp_path / "start-race.db"))

    async def scenario():
        results = await asyncio.gather(
            *[service.record_private_start(9999999) for _ in range(15)]
        )
        assert results.count(True) == 1
        assert results.count(False) == 14
        stats = await service.start_statistics()
        assert stats["unique_telegram_ids"] == 1
        assert stats["total_start_commands"] == 15

    run(scenario())


def test_telegram_delivery_failure_is_not_replayed_on_repeated_start(monkeypatch, tmp_path):
    service, _, middleware = setup(monkeypatch, tmp_path)
    bot = FakeTopicBot(fail_send=True)

    async def scenario():
        await service.configure_group(-100111, 999)
        handler = AsyncMock(return_value="ok")
        await middleware(handler, make_start(), {"bot": bot})
        assert len(bot.messages) == 2  # topic attempt + documented fallback
        await middleware(handler, make_start(), {"bot": bot})
        assert len(bot.messages) == 2  # never retry an ambiguous send on /start
        async with aiosqlite.connect(service.db_path) as conn:
            async with conn.execute(
                "SELECT start_count, delivery_status FROM group_ops_start_users"
            ) as cursor:
                assert await cursor.fetchone() == (2, "failed")

    run(scenario())


def test_start_before_group_connection_cannot_consume_first_report(monkeypatch, tmp_path):
    service, bot, middleware = setup(monkeypatch, tmp_path)

    async def scenario():
        handler = AsyncMock(return_value="ok")
        await middleware(handler, make_start(), {"bot": bot})
        assert (await service.start_statistics())["unique_telegram_ids"] == 0
        await service.configure_group(-100222, 999)
        await middleware(handler, make_start(), {"bot": bot})
        assert len(bot.messages) == 1
        await middleware(handler, make_start(), {"bot": bot})
        assert len(bot.messages) == 1

    run(scenario())


def test_start_role_is_resolved_after_rebecca_link(monkeypatch, tmp_path):
    service, bot, middleware = setup(monkeypatch, tmp_path)
    admins = []

    async def handler(_event, _data):
        admins.append(SimpleNamespace(is_active=True))
        return "linked"

    async def admin_list(_user_id):
        return list(admins)

    monkeypatch.setattr(group_ops_middleware.db, "get_admins_for_user", admin_list)
    monkeypatch.setattr(config, "PANEL_PROVIDER", "rebecca")

    async def scenario():
        await service.configure_group(-100333, 999)
        assert await middleware(handler, make_start(), {"bot": bot}) == "linked"
        assert "ادمین Rebecca" in bot.messages[0]["text"]
        assert "مشتری" not in bot.messages[0]["text"]

    run(scenario())


@pytest.mark.parametrize("text", [None, "", "  ", "/started", "hello", "/help"])
def test_non_start_messages_never_generate_telemetry(monkeypatch, tmp_path, text):
    service, bot, middleware = setup(monkeypatch, tmp_path)

    async def scenario():
        await service.configure_group(-100444, 999)
        handler = AsyncMock(return_value="ok")
        event = make_start(text=text)
        assert await middleware(handler, event, {"bot": bot}) == "ok"
        assert not bot.messages
        assert (await service.start_statistics())["unique_telegram_ids"] == 0
        handler.assert_awaited_once()

    run(scenario())


def test_invalid_numeric_ids_never_create_identity(tmp_path):
    service = GroupOpsService(str(tmp_path / "ids.db"))
    async def scenario():
        for bad in (0, -1, True):
            with pytest.raises(ValueError):
                await service.record_private_start(bad)
    run(scenario())
