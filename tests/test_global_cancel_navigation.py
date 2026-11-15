"""End-to-end navigation contracts for legacy buttons and global Cancel.

These test safe routing and UI behavior with mocks. Live Telegram/Rebecca
acceptance remains a separate release gate.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.types import (
    InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup,
)
import cancel_ui
from handlers import navigation_guard as nav
import kyc_middleware


def _markup(*pairs):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=text,callback_data=callback)]
        for text,callback in pairs
    ])


def _method(chat_id, markup, text="انتخاب کنید"):
    return SimpleNamespace(chat_id=chat_id,reply_markup=markup,text=text)


@pytest.mark.asyncio
async def test_private_menus_get_one_premium_cancel_without_mutating_callbacks(monkeypatch):
    styled=AsyncMock(side_effect=lambda text, **kwargs: InlineKeyboardButton(
        text="❌ "+text,callback_data=kwargs["callback_data"]))
    monkeypatch.setattr(cancel_ui.style_engine,"styled_button",styled)
    msg=_method(42,_markup(("🔙 بازگشت","wallet:accounts"),("📊 گزارش","wallet:reports")))
    await cancel_ui.decorate_cancel(msg)
    await cancel_ui.decorate_cancel(msg)
    callbacks=[b.callback_data for row in msg.reply_markup.inline_keyboard for b in row]
    assert callbacks==["wallet:accounts","wallet:reports","ui:cancel"]
    styled.assert_awaited_once()
    assert styled.await_args.kwargs["icon_key"]=="cancel"


@pytest.mark.asyncio
async def test_cancel_from_old_kyc_and_style_button_uses_new_root(monkeypatch):
    monkeypatch.setattr(cancel_ui.style_engine,"styled_button",AsyncMock(
        side_effect=lambda text, **kw: InlineKeyboardButton(
            text="لغو",callback_data=kw["callback_data"])))
    for old in ("kyc:cancel","style:cancel"):
        msg=_method(42,_markup(("❌ انصراف",old)))
        await cancel_ui.decorate_cancel(msg)
        keys=[x.callback_data for row in msg.reply_markup.inline_keyboard for x in row]
        assert keys==["ui:cancel"]


@pytest.mark.asyncio
async def test_private_home_remains_exactly_six_actions(monkeypatch):
    msg=_method(42,_markup(("گزارش‌ها","customer:reports"),
                           ("تعرفه","customer:explore"),
                           ("حساب","customer:account"),
                           ("کیف پول","wallet:home")))
    monkeypatch.setattr(cancel_ui.style_engine,"styled_button",AsyncMock())
    await cancel_ui.decorate_cancel(msg)
    assert sum(len(row) for row in msg.reply_markup.inline_keyboard)==4
    cancel_ui.style_engine.styled_button.assert_not_awaited()


@pytest.mark.asyncio
async def test_group_and_inline_messages_are_never_modified(monkeypatch):
    monkeypatch.setattr(cancel_ui.style_engine,"styled_button",AsyncMock())
    group=_method(-1007,_markup(("گزارش","ops:hub:reports")))
    inline=_method(None,_markup(("گزارش","ops:hub:reports")))
    await cancel_ui.decorate_cancel(group)
    await cancel_ui.decorate_cancel(inline)
    assert len(group.reply_markup.inline_keyboard)==1
    assert len(inline.reply_markup.inline_keyboard)==1


@pytest.mark.asyncio
async def test_native_reply_keyboard_gets_unicode_cancel_fallback():
    msg=_method(42,ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="ارسال گروه")]],
        resize_keyboard=True))
    await cancel_ui.decorate_cancel(msg)
    assert msg.reply_markup.keyboard[-1][0].text=="❌ لغو"


@pytest.mark.asyncio
async def test_plain_input_prompts_get_cancel_but_completed_notifications_do_not(monkeypatch):
    monkeypatch.setattr(cancel_ui.style_engine,"styled_button",AsyncMock(
        side_effect=lambda text, **kw: InlineKeyboardButton(
            text="❌ لغو",callback_data=kw["callback_data"])))
    # Real aiogram models use these method names.
    SendMessage=type("SendMessage",(),{})
    prompt=SendMessage()
    prompt.chat_id=42
    prompt.reply_markup=None
    prompt.text="لطفاً مبلغ را به تومان وارد کنید:"
    await cancel_ui.decorate_cancel(prompt)
    assert prompt.reply_markup.inline_keyboard[-1][0].callback_data=="ui:cancel"
    notification=SendMessage()
    notification.chat_id=42
    notification.reply_markup=None
    notification.text="✅ پرداخت تأیید شد. لطفاً اطلاعات ورود را نگه‌داری کنید."
    await cancel_ui.decorate_cancel(notification)
    assert notification.reply_markup is None


def _cb(data="ui:cancel", user=42):
    return SimpleNamespace(
        data=data,from_user=SimpleNamespace(id=user),
        message=SimpleNamespace(
            chat=SimpleNamespace(type="private"),
            edit_text=AsyncMock(), answer=AsyncMock(),
        ),
        answer=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_cancel_clears_fsm_and_returns_to_role_aware_home(monkeypatch):
    home=AsyncMock()
    from handlers import home_navigation
    monkeypatch.setattr(home_navigation,"render_home",home)
    cb=_cb()
    state=SimpleNamespace(clear=AsyncMock())
    await nav.cancel_current_screen(cb,state)
    state.clear.assert_awaited_once()
    home.assert_awaited_once_with(cb.message,42,edit=True)
    cb.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_old_home_and_panel_roots_never_enter_legacy_handlers(monkeypatch):
    from handlers import home_navigation, payg_customer_center
    home=AsyncMock()
    hub=AsyncMock()
    monkeypatch.setattr(home_navigation,"render_home",home)
    monkeypatch.setattr(payg_customer_center,"render_accounts",hub)
    state=SimpleNamespace(clear=AsyncMock())
    for key in ("back_to_main","back_to_admin_main","public_back_main","svcmarket:home"):
        cb=_cb(key)
        await nav.unified_legacy_home(cb,state)
    assert home.await_count==4
    for key in ("my_info","my_report","my_users","admin_renew","reactivate_users"):
        cb=_cb(key)
        await nav.unified_legacy_panel_roots(cb,state)
    assert hub.await_count==5
    assert all(call.args[1]==42 for call in hub.await_args_list)


@pytest.mark.asyncio
async def test_old_provider_picker_goes_to_new_purchase_hub(monkeypatch):
    from handlers import payg_offers
    from handlers import navigation_guard
    monkeypatch.setattr(navigation_guard.config,"PANEL_PROVIDER","rebecca")
    render=AsyncMock()
    monkeypatch.setattr(payg_offers,"render_purchase_method",render)
    cb=_cb("panelprov:select:a:rebecca")
    state=SimpleNamespace(clear=AsyncMock())
    await nav.retired_provider_picker(cb,state)
    render.assert_awaited_once_with(cb.message,"a")
    cb.answer.assert_awaited_once()


def test_navigation_guard_router_registered_before_legacy_stateful_routers():
    source=open("handlers/__init__.py",encoding="utf-8").read()
    assert source.index("style_admin_router.include_router(navigation_guard_router)") < source.index(
        "style_admin_router.include_router(group_ops_router)")
    assert 'navigation_guard_router = Router(name="canonical_navigation_guard")' in open(
        "handlers/navigation_guard.py",encoding="utf-8").read()


def test_password_redisplay_is_subject_to_same_panel_kyc_gate():
    assert kyc_middleware._target_admin_id("rebecca:credentials:42")==42
    assert kyc_middleware._target_admin_id("rebecca:credentials:not-a-number") is None
