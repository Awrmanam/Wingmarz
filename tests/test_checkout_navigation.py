import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from aiogram.types import InlineKeyboardButton
import config
from models.schemas import PlanModel
from checkout_presentation import plan_summary
from premium_ui_service import PremiumUIService
from handlers import ui_editor_v2 as editor
from handlers import service_marketplace as market
from handlers import trial_experience as purchase


def run(coro):
    return asyncio.run(coro)


async def button(text,callback_data=None,**kwargs):
    return InlineKeyboardButton(text=text,callback_data=callback_data)


def test_summary_shows_commercial_limits_without_internal_identity():
    plan=PlanModel(id=88,name='<Gold>',price=120000,traffic_limit_bytes=2*1024**3,time_limit_seconds=30*86400,max_users=5,rebecca_service_ids='31337')
    body=plan_summary(plan)
    for value in ['&lt;Gold&gt;','2 GB','30 روز','5 کاربر','120,000']:
        assert value in body
    assert '31337' not in body and '88' not in body


def test_unlimited_values_are_not_shown_as_zero():
    body=plan_summary(PlanModel(name='Gold',price=100))
    assert body.count('نامحدود') == 3


def test_editor_draft_does_not_change_runtime_until_save(tmp_path,monkeypatch):
    monkeypatch.setitem(config.MESSAGES,'customer_home','original')
    service=PremiumUIService(str(tmp_path/'editor.db'))
    monkeypatch.setattr(editor,'premium_ui_service',service)
    monkeypatch.setattr(editor,'_sudo',lambda _id:True)
    monkeypatch.setattr(editor,'_btn',button)
    data={'uiv2_message_key':'customer_home'}
    async def update(**kwargs): data.update(kwargs)
    state=SimpleNamespace(get_data=AsyncMock(side_effect=lambda:dict(data)),update_data=update,clear=AsyncMock())
    message=SimpleNamespace(from_user=SimpleNamespace(id=1),text='<b>draft</b>',answer=AsyncMock())
    async def scenario():
        await editor.message_edit_value(message,state)
        assert config.MESSAGES['customer_home']=='original'
        assert data['uiv2_message_draft']=='<b>draft</b>'
        assert message.answer.call_args.args[0]=='<b>draft</b>'
        monkeypatch.setattr(editor,'_deny',AsyncMock(return_value=False))
        monkeypatch.setattr(editor,'_render_message_detail',AsyncMock())
        callback=SimpleNamespace(from_user=message.from_user,message=message,answer=AsyncMock())
        await editor.message_save_draft(callback,state)
        assert config.MESSAGES['customer_home']=='<b>draft</b>'
        state.clear.assert_awaited_once()
    run(scenario())


def test_single_service_and_duration_skip_picker_with_valid_home_back(monkeypatch):
    service=SimpleNamespace(id=9,rebecca_service_id=31337,display_name='WireGuard')
    plan=PlanModel(id=5,name='Gold',price=100,time_limit_seconds=86400)
    monkeypatch.setattr(market.service_marketplace_service,'sellable_services',AsyncMock(return_value=[(service,1)]))
    monkeypatch.setattr(market.service_marketplace_service,'plans_for_service',AsyncMock(return_value=[plan]))
    monkeypatch.setattr(market.service_marketplace_service,'get_service_by_catalog_id',AsyncMock(return_value=service))
    monkeypatch.setattr(market.service_marketplace_service,'duration_groups_enabled',AsyncMock(return_value=True))
    monkeypatch.setattr(market,'_button',button)
    message=SimpleNamespace(edit_text=AsyncMock())
    run(market._render_purchase_services(message,'p'))
    callbacks=[b.callback_data for row in message.edit_text.call_args.kwargs['reply_markup'].inline_keyboard for b in row]
    assert callbacks==['public_order_5','public_back_main']
    assert '31337' not in message.edit_text.call_args.args[0]


def test_purchase_shows_summary_before_username_without_creating_order(monkeypatch):
    plan=PlanModel(id=5,name='Gold',price=500,traffic_limit_bytes=1024**3)
    monkeypatch.setattr(purchase.db,'get_plan_by_id',AsyncMock(return_value=plan))
    add=AsyncMock()
    monkeypatch.setattr(purchase.db,'add_order',add)
    monkeypatch.setattr(purchase,'_button',button)
    state=SimpleNamespace(clear=AsyncMock(),update_data=AsyncMock(),set_state=AsyncMock())
    callback=SimpleNamespace(message=SimpleNamespace(edit_text=AsyncMock()),answer=AsyncMock())
    run(purchase._start_purchase(callback,state,'p',5))
    assert '1 GB' in callback.message.edit_text.call_args.args[0]
    assert '500' in callback.message.edit_text.call_args.args[0]
    add.assert_not_awaited()
