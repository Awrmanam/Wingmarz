"""Only one new Fastly action is added to each existing customer homepage."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.types import InlineKeyboardButton

from handlers import home_keyboards
from handlers.fastly_store import FastlyPrivateMiddleware


def run(coro):
    return asyncio.run(coro)


def _callbacks(markup):
    return [[button.callback_data for button in row] for row in markup.inline_keyboard]


def test_customer_home_keeps_all_legacy_actions(monkeypatch):
    async def stub(text, *, callback_data, **kwargs):
        return InlineKeyboardButton(text=text, callback_data=callback_data)
    monkeypatch.setattr(home_keyboards.style_engine, "styled_button", stub)
    public = run(home_keyboards.public_home_keyboard())
    reseller = run(home_keyboards.reseller_home_keyboard())
    assert _callbacks(public)==[
        ["public_buy_reseller"],
        ["fastly:home"],
        ["tariffs:home","svcmarket:trial"],
        ["kyc:status","support:home"],
    ]
    assert _callbacks(reseller)==[
        ["my_info","my_report"],["my_users","reactivate_users"],
        ["admin_buy_reseller","admin_renew"],["fastly:home"],
        ["tariffs:home","svcmarket:trial"],["kyc:status","support:home"],
    ]


def test_fastly_private_chat_guard_blocks_group_and_other_users():
    async def scenario():
        middleware=FastlyPrivateMiddleware()
        handler=AsyncMock()
        message=SimpleNamespace(chat=SimpleNamespace(type="supergroup",id=-100))
        event=SimpleNamespace(message=message,from_user=SimpleNamespace(id=123),
                              data="fastly:accept:1",answer=AsyncMock())
        await middleware(handler,event,{})
        handler.assert_not_awaited()
        event.answer.assert_awaited_once()
        message.chat=SimpleNamespace(type="private",id=999)
        await middleware(handler,event,{})
        handler.assert_not_awaited()
        message.chat.id=123
        event.data="fastly:orders"
        await middleware(handler,event,{})
        handler.assert_awaited_once()
    run(scenario())
