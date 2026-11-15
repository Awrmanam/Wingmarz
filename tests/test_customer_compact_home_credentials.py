"""Compact customer navigation and owner-only Rebecca login redisplay."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.types import InlineKeyboardButton

from handlers import home_navigation, home_keyboards
from handlers import rebecca_admin_sync as live


def callback(*, owner=42, chat_type="private", data="rebecca:credentials:7"):
    return SimpleNamespace(
        data=data,
        from_user=SimpleNamespace(id=owner),
        message=SimpleNamespace(
            chat=SimpleNamespace(type=chat_type),
            edit_text=AsyncMock(),
        ),
        answer=AsyncMock(),
        bot=SimpleNamespace(send_message=AsyncMock()),
    )


def panel(*, owner=42, password="abc<&secret"):
    return SimpleNamespace(
        id=7,
        user_id=owner,
        admin_name="ProMax",
        marzban_username="promax-test",
        marzban_password=password,
        login_url="https://panel.example.com",
        is_active=True,
    )


@pytest.mark.asyncio
async def test_private_owner_can_read_saved_password_with_spoiler(monkeypatch):
    person=panel()
    monkeypatch.setattr(live.db,"get_admin_by_id",AsyncMock(return_value=person))
    query=callback()

    await live.rebecca_panel_credentials(query)

    query.bot.send_message.assert_awaited_once()
    args=query.bot.send_message.await_args
    assert args.args[0] == 42
    body=args.args[1]
    assert "<tg-spoiler>abc&lt;&amp;secret</tg-spoiler>" in body
    assert "<code>promax-test</code>" in body
    assert "https://panel.example.com" in body
    assert args.kwargs["parse_mode"] == "HTML"
    assert args.kwargs["reply_markup"].inline_keyboard[0][0].callback_data == "customer:fixed:7"


@pytest.mark.asyncio
async def test_private_other_user_cannot_read_password(monkeypatch):
    find=AsyncMock(return_value=panel())
    monkeypatch.setattr(live.db,"get_admin_by_id",find)
    query=callback(owner=777)
    await live.rebecca_panel_credentials(query)
    find.assert_awaited_once_with(7)
    query.bot.send_message.assert_not_awaited()
    assert "دسترسی" in query.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_password_is_never_sent_to_group_or_supergroup(monkeypatch):
    find=AsyncMock()
    monkeypatch.setattr(live.db,"get_admin_by_id",find)
    query=callback(chat_type="supergroup")
    await live.rebecca_panel_credentials(query)
    find.assert_not_awaited()
    query.bot.send_message.assert_not_awaited()
    assert "خصوصی" in query.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_missing_password_fails_closed_without_inventing_credentials(monkeypatch):
    monkeypatch.setattr(live.db,"get_admin_by_id",AsyncMock(return_value=panel(password=None)))
    query=callback()
    await live.rebecca_panel_credentials(query)
    query.bot.send_message.assert_not_awaited()
    assert "ذخیره نشده" in query.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_info_page_offers_password_button_even_when_rebecca_is_unavailable(monkeypatch):
    monkeypatch.setattr(live,"_live_panel",AsyncMock(side_effect=Exception("offline")))
    monkeypatch.setattr(live,"_panel_back_keyboard",AsyncMock(
        return_value=SimpleNamespace(inline_keyboard=[[InlineKeyboardButton(
            text="بازگشت",callback_data="customer:fixed:7")]])
    ))
    query=callback()
    await live._render_info(query,panel())
    keys=[b.callback_data for row in query.message.edit_text.await_args.kwargs[
        "reply_markup"].inline_keyboard for b in row]
    assert "rebecca:credentials:7" in keys
    assert "customer:fixed:7" in keys


@pytest.mark.asyncio
async def test_home_hub_is_role_aware_and_preserves_old_actions(monkeypatch):
    build=AsyncMock(return_value=SimpleNamespace(inline_keyboard=[]))
    monkeypatch.setattr(home_navigation,"customer_hub_keyboard",build)
    monkeypatch.setattr(home_navigation,"has_admin_account",AsyncMock(return_value=True))
    query=callback(data="customer:reports")
    state=SimpleNamespace(clear=AsyncMock())
    await home_navigation.customer_secondary_hub(query,state)
    state.clear.assert_awaited_once()
    build.assert_awaited_once_with("reports",reseller=True)
    assert "گزارش‌های من" in query.message.edit_text.await_args.args[0]
    query.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_public_customer_hub_hides_panel_only_actions(monkeypatch):
    async def button(text, **kwargs):
        return InlineKeyboardButton(text=text,callback_data=kwargs["callback_data"])
    monkeypatch.setattr(home_keyboards.style_engine,"styled_button",button)
    keyboard=await home_keyboards.customer_hub_keyboard("account",reseller=False)
    keys=[b.callback_data for row in keyboard.inline_keyboard for b in row]
    assert {"kyc:status","support:home","back_to_admin_main"}.issubset(keys)
    assert not {"my_info","my_users","reactivate_users"}.intersection(keys)
