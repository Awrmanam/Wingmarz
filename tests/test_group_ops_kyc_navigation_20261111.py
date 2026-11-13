"""Regression coverage for Group Ops delivery and KYC navigation on fresh installs."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from handlers import group_ops, kyc, operations
from group_ops_service import GroupOpsService, TOPICS


def run(coro):
    return asyncio.run(coro)


def callback(*, chat_type="private", user_id=42, bot=None):
    return SimpleNamespace(
        from_user=SimpleNamespace(id=user_id),
        message=SimpleNamespace(
            chat=SimpleNamespace(type=chat_type),
            edit_text=AsyncMock(),
        ),
        bot=bot,
        answer=AsyncMock(),
    )


def test_voluntary_kyc_available_before_any_purchase(monkeypatch):
    approved = AsyncMock(return_value=False)
    monkeypatch.setattr(kyc.trust_service, "is_identity_approved", approved)
    monkeypatch.setattr(kyc.kyc_service, "status", AsyncMock(return_value="not_submitted"))
    monkeypatch.setattr(kyc.style_engine, "render_emoji", AsyncMock(return_value="🪪"))
    cb = callback()
    state = SimpleNamespace(clear=AsyncMock(), set_state=AsyncMock(), update_data=AsyncMock())

    run(kyc.kyc_start(cb, state))

    approved.assert_awaited_once_with(42)
    state.clear.assert_awaited_once()
    state.set_state.assert_awaited_once_with(kyc.KycStates.waiting_full_name)
    cb.message.edit_text.assert_awaited_once()
    assert "مرحله ۱ از ۳" in cb.message.edit_text.await_args.args[0]


def test_kyc_document_request_is_private_only(monkeypatch):
    approved = AsyncMock(return_value=False)
    monkeypatch.setattr(kyc.trust_service, "is_identity_approved", approved)
    cb = callback(chat_type="supergroup")
    state = SimpleNamespace(clear=AsyncMock(), set_state=AsyncMock(), update_data=AsyncMock())

    run(kyc.kyc_start(cb, state))

    cb.answer.assert_awaited_once()
    assert cb.answer.await_args.kwargs["show_alert"] is True
    approved.assert_not_awaited()
    state.clear.assert_not_awaited()


def test_approved_profile_cannot_restart_kyc_through_user_interface(monkeypatch):
    monkeypatch.setattr(kyc.trust_service, "is_identity_approved", AsyncMock(return_value=True))
    cb = callback()
    state = SimpleNamespace(clear=AsyncMock(), set_state=AsyncMock(), update_data=AsyncMock())

    run(kyc.kyc_start(cb, state))

    assert "قبلاً" in cb.answer.await_args.args[0]
    state.set_state.assert_not_awaited()


def test_pending_kyc_navigation_is_in_customer_hub():
    source = __import__("inspect").getsource(kyc.kyc_pending)
    assert "ops:hub:customers" in source
    assert "sudo_manage_admins" not in source


def test_admin_management_has_single_entry():
    quick = [item[0] for item in operations.QUICK_ACTIONS]
    panels = [item[0] for item in operations.HUB_MENUS["panels"]["items"]]
    assert "gops:admins:0" not in quick
    assert panels.count("resellercenter:home") == 1
    assert panels.count("gops:admins:0") == 0


class TopicBot:
    def __init__(self):
        self.created = []
        self.sent = []

    async def create_forum_topic(self, *, chat_id, name):
        self.created.append((chat_id, name))
        return SimpleNamespace(message_thread_id=200 + len(self.created))

    async def send_message(self, **kwargs):
        self.sent.append(kwargs)
        return SimpleNamespace(message_id=len(self.sent))


def test_group_delivery_probe_uses_nine_distinct_threads(tmp_path, monkeypatch):
    service = GroupOpsService(str(tmp_path / "ops.sqlite3"))
    run(service.configure_group(-100555, 42))
    monkeypatch.setattr(group_ops, "group_ops_service", service)
    monkeypatch.setattr(group_ops, "_deny_event", AsyncMock(return_value=False))
    bot = TopicBot()
    cb = callback(bot=bot)

    run(group_ops.ops_test_topics(cb))

    assert len(bot.created) == len(TOPICS) == 9
    assert len(bot.sent) == 9
    assert {message["message_thread_id"] for message in bot.sent} == set(range(201, 210))
    assert all(message["chat_id"] == -100555 for message in bot.sent)
    assert "9/9" in cb.message.edit_text.await_args.args[0]
    cb.answer.assert_awaited_once()


def test_photo_review_back_opens_list_without_editing_media(monkeypatch):
    monkeypatch.setattr(kyc.config, "SUDO_ADMINS", [42])
    monkeypatch.setattr(kyc.kyc_service, "list_pending", AsyncMock(return_value=[]))
    cb = callback()
    cb.message.photo = [object()]
    cb.message.answer = AsyncMock()

    run(kyc.kyc_pending(cb))

    cb.message.answer.assert_awaited_once()
    assert "KYCهای در انتظار" in cb.message.answer.await_args.args[0]
    cb.message.edit_text.assert_not_awaited()
    cb.answer.assert_awaited_once()


def test_text_kyc_pending_navigation_edits_in_place(monkeypatch):
    monkeypatch.setattr(kyc.config, "SUDO_ADMINS", [42])
    monkeypatch.setattr(kyc.kyc_service, "list_pending", AsyncMock(return_value=[]))
    cb = callback()
    cb.message.photo = None
    cb.message.answer = AsyncMock()

    run(kyc.kyc_pending(cb))

    cb.message.edit_text.assert_awaited_once()
    cb.message.answer.assert_not_awaited()
