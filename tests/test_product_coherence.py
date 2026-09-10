import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import time
import aiosqlite
import pytest
from aiogram.types import InlineKeyboardButton
import config
from database import Database
from operations_service import OperationsService, OperationsError
from premium_ui_service import PremiumUIService, PremiumUIError, ButtonCatalogItem
from text_templates import validate_template
from trial_delivery import connection_url, save_connections, load_connections
from handlers import trial_ui_v2 as trial
from handlers import trial_experience as purchase
from handlers import service_marketplace as market
from handlers import public_handlers as public
from handlers import control_center as cc
from handlers.premium_ui_clean_buttons import _is_dynamic_noise
from order_queries import list_orders
import support_service


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    path = str(tmp_path / 'product.db')
    monkeypatch.setattr(config, 'DATABASE_PATH', path)
    return path


async def fake_button(text, callback_data=None, **kwargs):
    return InlineKeyboardButton(text=text, callback_data=callback_data, url=kwargs.get('url'))


@pytest.mark.parametrize('body', ['{traffic}', '{minutes}', '{traffic} {minutes} {unknown}',
    '{traffic.__class__} {minutes}', '{traffic!r} {minutes}', '{traffic} {minutes} <b>open',
    '{traffic} {minutes} <script>x</script>', '{traffic} {minutes} <a href="javascript:alert(1)">x</a>'])
def test_invalid_templates_cannot_replace_runtime_or_persist(body, isolated, monkeypatch):
    monkeypatch.setitem(config.MESSAGES, 'test_template', '{traffic} {minutes}')
    service = PremiumUIService(isolated)
    async def scenario():
        with pytest.raises(PremiumUIError):
            await service.set_message('test_template', body)
        assert config.MESSAGES['test_template'] == '{traffic} {minutes}'
        await service.ensure_schema()
        async with aiosqlite.connect(isolated) as conn:
            assert (await (await conn.execute('SELECT COUNT(*) FROM styled_message_overrides')).fetchone())[0] == 0
    run(scenario())


def test_valid_html_and_emoji_survive_persistence_and_formatting(isolated, monkeypatch):
    monkeypatch.setitem(config.MESSAGES, 'test_template', '{username}')
    service = PremiumUIService(isolated)
    async def scenario():
        await service.set_message('test_template', '{emoji:success} <b>{username}</b>')
        await service.apply_message_overrides()
        assert config.MESSAGES['test_template'].format(username='Arman') == '{emoji:success} <b>Arman</b>'
    run(scenario())


def test_invalid_historic_override_falls_back_without_destroying_it(isolated, monkeypatch):
    monkeypatch.setitem(config.MESSAGES, 'test_template', '{username}')
    service = PremiumUIService(isolated)
    async def scenario():
        await service.ensure_schema()
        async with aiosqlite.connect(isolated) as conn:
            await conn.execute("INSERT INTO styled_message_overrides(message_key,body) VALUES('test_template','{bad}')")
            await conn.commit()
        await service.apply_message_overrides()
        assert config.MESSAGES['test_template'] == '{username}'
        assert (await service.get_message('test_template')).body == '{bad}'
    run(scenario())


@pytest.mark.parametrize('url', ['javascript:alert(1)', 'https://', 'https://user:pass@example.com', 'https://example.com:bad', 'https://example.com/\nfoo', 'file:///etc/passwd'])
def test_reject_unsafe_connection_urls(url):
    assert connection_url(url) is None


@pytest.mark.parametrize('links,subscription', [(['vless://one', 'vmess://two'], None), (['https://example.org/extra'], 'https://example.org/sub')])
def test_clean_trial_card_never_prints_internal_metadata(links, subscription, isolated, monkeypatch):
    monkeypatch.setattr(trial, '_button', fake_button)
    monkeypatch.setattr(trial, '_template', AsyncMock(return_value='✅ کانفیگ تست آماده است'))
    message = SimpleNamespace(edit_text=AsyncMock())
    result = dict(username='test_secret', service_id=344, service_name='Wire', links=links,
                  subscription_url=subscription, expire_at=int(time.time())+3600, traffic_bytes=1024**3)
    run(trial.render_config_result(message, 7, result, edit=True))
    text = message.edit_text.call_args.args[0]
    for secret in ['test_secret', '344', 'Wire', *links]:
        assert secret not in text
    rows = message.edit_text.call_args.kwargs['reply_markup'].inline_keyboard
    assert len(rows) == 2
    assert rows[-1][0].callback_data == 'trialv2:root'
    if subscription:
        assert rows[0][0].url == subscription
    else:
        assert rows[0][0].callback_data.startswith('trialv2:links:')


def test_raw_trial_delivery_is_owner_bound_and_expires(isolated):
    async def scenario():
        token = await save_connections(7, {'links':['vless://private'], 'expire_at': int(time.time())+600})
        assert await load_connections(token,8) == []
        assert await load_connections(token,7) == ['vless://private']
        async with aiosqlite.connect(isolated) as conn:
            await conn.execute('UPDATE trial_connections SET expires=0')
            await conn.commit()
        assert await load_connections(token,7) == []
    run(scenario())


def test_marzban_purchase_uses_existing_picker(monkeypatch):
    monkeypatch.setattr(config, 'PANEL_PROVIDER', 'marzban')
    old = AsyncMock()
    monkeypatch.setattr(public, 'public_buy_reseller', old)
    rebecca = AsyncMock()
    monkeypatch.setattr(market, '_render_purchase_services', rebecca)
    callback = SimpleNamespace(message=object(), answer=AsyncMock())
    run(market.service_buy_public(callback, SimpleNamespace(clear=AsyncMock())))
    old.assert_awaited_once_with(callback)
    rebecca.assert_not_awaited()


def test_public_home_has_one_trial_and_no_unavailable_admin_controls():
    callbacks = [b.callback_data for row in public.get_public_main_keyboard().inline_keyboard for b in row]
    assert callbacks.count('svcmarket:trial') == 1
    assert 'support:home' in callbacks
    assert 'my_users' not in callbacks


def test_checkout_button_passes_clicking_customer_not_message_author(monkeypatch):
    render = AsyncMock()
    monkeypatch.setattr(purchase, '_create_order_and_render', render)
    state = SimpleNamespace(get_data=AsyncMock(return_value={'purchase_plan_id':1, 'purchase_source':'p','purchase_username':'arman'}), clear=AsyncMock())
    callback = SimpleNamespace(from_user=SimpleNamespace(id=42), message=SimpleNamespace(from_user=SimpleNamespace(id=999)), answer=AsyncMock())
    run(purchase.preferred_checkout_nocode(callback,state))
    assert render.call_args.kwargs['user_id'] == 42


def test_order_creation_uses_explicit_customer(isolated, monkeypatch):
    fake_db = SimpleNamespace(get_plan_by_id=AsyncMock(return_value=SimpleNamespace(id=1,is_active=True,price=100,name='Gold')),
        add_order=AsyncMock(return_value=1),get_cards=AsyncMock(return_value=[]))
    monkeypatch.setattr(purchase, 'db', fake_db)
    monkeypatch.setattr(purchase.trial_experience_service, 'username_available', AsyncMock(return_value=True))
    monkeypatch.setattr(purchase.trial_experience_service, 'save_order_username', AsyncMock())
    monkeypatch.setattr(purchase, '_button', fake_button)
    message=SimpleNamespace(from_user=SimpleNamespace(id=999),answer=AsyncMock())
    run(purchase._create_order_and_render(message,'p',1,'arman',None,user_id=42))
    assert fake_db.add_order.call_args.args[0] == 42
    assert 'شناسه سفارش' not in message.answer.call_args.args[0]


def test_receipt_transition_is_atomic_and_owner_checked(isolated):
    db=Database(isolated)
    async def scenario():
        await db.init_db()
        async with aiosqlite.connect(isolated) as conn:
            await conn.execute("INSERT INTO orders(id,user_id,plan_id,status) VALUES(1,7,1,'pending')")
            await conn.commit()
        assert not await db.submit_order_receipt(1,8,'stolen')
        result = await asyncio.gather(db.submit_order_receipt(1,7,'a'),db.submit_order_receipt(1,7,'b'))
        assert sum(result) == 1
        await db.update_order(1,status='approved')
        assert not await db.submit_order_receipt(1,7,'overwrite')
        assert (await db.get_order_by_id(1))['status'] == 'approved'
    run(scenario())


def test_discount_last_slot_is_reserved_once_and_retries_idempotent(isolated):
    service=OperationsService(isolated)
    async def scenario():
        await Database(isolated).init_db()
        await service.create_discount(code='LAST',kind='percent',value=10,max_uses=1)
        async with aiosqlite.connect(isolated) as conn:
            await conn.executemany('INSERT INTO orders(id,user_id,plan_id) VALUES(?,?,1)',[(1,7),(2,8)])
            await conn.commit()
        quotes=[await service.quote_discount('LAST',u,100) for u in (7,8)]
        results=await asyncio.gather(*(service.record_redemption(q,u,o) for q,u,o in zip(quotes,(7,8),(1,2))),return_exceptions=True)
        assert sum(isinstance(r,OperationsError) for r in results)==1
        winner=next(i for i,r in enumerate(results) if r is None)
        await service.record_redemption(quotes[winner],7+winner,1+winner)
        async with aiosqlite.connect(isolated) as conn:
            assert (await (await conn.execute('SELECT COUNT(*) FROM discount_redemptions')).fetchone())[0]==1
    run(scenario())


def test_discount_start_time_and_changed_rule_are_rechecked(isolated):
    service=OperationsService(isolated)
    async def scenario():
        await Database(isolated).init_db()
        await service.create_discount(code='LATER',kind='percent',value=10,starts_at=int(time.time())+86400)
        with pytest.raises(OperationsError):
            await service.quote_discount('LATER',7,100)
        assert not await service.has_active_discounts(100)
        await service.create_discount(code='NOW',kind='percent',value=10)
        quote=await service.quote_discount('NOW',7,100)
        async with aiosqlite.connect(isolated) as conn:
            await conn.execute('INSERT INTO orders(id,user_id,plan_id) VALUES(1,7,1)')
            await conn.execute("UPDATE discount_codes SET value=20 WHERE code='NOW'")
            await conn.commit()
        with pytest.raises(OperationsError):
            await service.record_redemption(quote,7,1)
    run(scenario())


def test_manager_cannot_grant_revoke_or_remove_owner(isolated):
    service=OperationsService(isolated)
    async def scenario():
        with pytest.raises(OperationsError):
            await service.add_runtime_admin(8,7)
        with pytest.raises(OperationsError):
            await service.set_runtime_admin_active(8,False,actor_id=7)
        with pytest.raises(OperationsError):
            await service.remove_runtime_admin(service.base_sudo_ids[0],actor_id=7)
        with pytest.raises(OperationsError):
            await service.remove_runtime_admin(service.base_sudo_ids[0],actor_id=service.base_sudo_ids[0])
    run(scenario())


@pytest.mark.parametrize('callback', ['uiv2:mc:trial','public_order_123','svcmarket:s:p:1','cc:ticket:4','select_panel_username'])
def test_dynamic_or_editor_buttons_are_not_offered_for_customization(callback):
    item=ButtonCatalogItem(1,callback,'some user',None,None,None,None)
    assert _is_dynamic_noise(item)


def test_button_reset_clears_all_legacy_variants_and_keeps_callbacks(isolated):
    service=PremiumUIService(isolated)
    async def scenario():
        await service.catalog_button('home:go','خانه',None,None)
        await service.catalog_button('home:go','🏠 خانه',None,None)
        items,_=await service.list_buttons()
        await service.set_button_text(items[0].id,'خانه جدید')
        for item in items:
            changed=await service.get_button(item.id)
            assert changed.display_text=='خانه جدید'
            assert changed.callback_data=='home:go'
        await service.reset_button_text(items[0].id)
        for item in items:
            assert (await service.get_button(item.id)).display_text is None
    run(scenario())


def test_ticket_reply_history_stays_open_until_explicit_close(isolated):
    async def scenario():
        await support_service.ensure_schema()
        async with aiosqlite.connect(isolated) as conn:
            await conn.execute("INSERT INTO support_tickets(id,user_id,subject,body) VALUES(1,7,'help','please')")
            await conn.commit()
        assert await support_service.reply_ticket(1,10,'first')==7
        assert await support_service.reply_ticket(1,10,'second')==7
        assert await support_service.history(1)==['first','second']
        async with aiosqlite.connect(isolated) as conn:
            await conn.execute("UPDATE support_tickets SET status='closed'")
            await conn.commit()
        assert await support_service.reply_ticket(1,10,'late') is None
        assert await support_service.history(1)==['first','second']
    run(scenario())


def test_order_filters_match_existing_states_without_rewriting_them(isolated):
    async def scenario():
        await Database(isolated).init_db()
        async with aiosqlite.connect(isolated) as conn:
            await conn.executemany('INSERT INTO orders(user_id,plan_id,status) VALUES(7,1,?)',[(s,) for s in ['pending','submitted','approved','rejected']])
            await conn.commit()
        assert (await list_orders('pending'))[1]==2
        assert (await list_orders('receipt'))[1]==1
        assert (await list_orders('approval'))[1]==1
        with pytest.raises(ValueError):
            await list_orders("' OR 1=1")
    run(scenario())


def test_customer_back_clears_active_fsm(monkeypatch):
    state=SimpleNamespace(clear=AsyncMock())
    callback=SimpleNamespace(message=SimpleNamespace(edit_text=AsyncMock()),answer=AsyncMock())
    run(public.public_back_main(callback,state))
    state.clear.assert_awaited_once()


def test_static_button_catalog_is_ready_without_visiting_every_menu(isolated):
    service = PremiumUIService(isolated)
    async def scenario():
        await service.seed_static_buttons()
        async with aiosqlite.connect(isolated) as conn:
            rows = await (await conn.execute('SELECT callback_data FROM styled_button_catalog')).fetchall()
        callbacks = {r[0] for r in rows}
        assert {'public_buy_reseller','support:home','trialv2:root','style:menu'} <= callbacks
        assert not any(c.startswith(('pui:','puc:','uiv2:','uiv3:','uiv4:')) for c in callbacks)
        assert not any(c.startswith('style:') and c != 'style:menu' for c in callbacks)
    run(scenario())


def test_legacy_trial_callbacks_resolve_to_clean_handlers():
    from aiogram.types import CallbackQuery, User
    async def scenario():
        for payload, name in [('svcmarket:trialcfg:2','issue_config_trial'),('svcmarket:trialpanel:2','panel_trial_selected')]:
            event = CallbackQuery(id='1',from_user=User(id=7,is_bot=False,first_name='A'),chat_instance='1',data=payload)
            matched=[]
            for handler in trial.trial_ui_v2_router.callback_query.handlers:
                passed,_ = await handler.check(event)
                if passed:
                    matched.append(handler.callback.__name__)
            assert matched == [name]
    run(scenario())


def test_editor_variables_come_from_defaults_and_customer_texts_are_registered():
    from text_templates import fields
    from handlers.ui_editor_v2 import _message_category, _message_title
    service = PremiumUIService()
    assert fields(service._base_messages['support_reply']) == {'reply'}
    assert fields(service._base_messages['trial_v2_panel_success']) == {'username','password','hours'}
    assert _message_category('support_reply') == 'support'
    assert _message_title('support_reply') == 'پاسخ پشتیبانی'


def test_revoked_dynamic_admin_is_removed_after_database_sync(isolated, monkeypatch):
    monkeypatch.setattr(config,'SUDO_ADMINS',list(config.SUDO_ADMINS))
    service=OperationsService(isolated)
    owner=service.base_sudo_ids[0]
    async def scenario():
        await service.add_runtime_admin(8001,owner)
        assert 8001 in config.SUDO_ADMINS
        row=(await service.list_runtime_admins())[0]
        assert row['role']=='manager'
        await service.set_runtime_admin_active(8001,False,actor_id=owner)
        await OperationsService(isolated).sync_runtime_admins()
        assert 8001 not in config.SUDO_ADMINS
        assert owner in config.SUDO_ADMINS
    run(scenario())
