import asyncio

import aiosqlite

import config
from premium_template_runtime import PremiumTemplateString
from premium_ui_service import PremiumUIService
from style_engine import StyledEmoji, style_engine


def test_premium_template_string_preserves_emoji_token_during_format():
    template = PremiumTemplateString("{emoji:wire} پنل {username} آماده است")
    rendered = template.format(username="arman")
    assert rendered == "{emoji:wire} پنل arman آماده است"
    assert isinstance(rendered, PremiumTemplateString)


def test_render_placeholders_uses_registered_emoji(monkeypatch, tmp_path):
    service = PremiumUIService(str(tmp_path / "pui.db"))
    item = StyledEmoji(id=1, key="wire", custom_emoji_id="123456", fallback_unicode="⚡", enabled=True)

    async def fake_get(key):
        return item if key == "wire" else None

    async def fake_render(key, fallback=None):
        assert key == "wire"
        return '<tg-emoji emoji-id="123456">⚡</tg-emoji>'

    monkeypatch.setattr(style_engine, "get_emoji", fake_get)
    monkeypatch.setattr(style_engine, "render_emoji", fake_render)

    rendered = asyncio.run(service.render_placeholders("{emoji:wire} اتصال آماده است {emoji:missing}"))
    assert '<tg-emoji emoji-id="123456">⚡</tg-emoji>' in rendered
    assert "{emoji:missing}" in rendered


def test_button_catalog_and_text_override_are_persistent(tmp_path):
    service = PremiumUIService(str(tmp_path / "pui.db"))

    async def scenario():
        await service.ensure_schema()
        await service.catalog_button("test:callback", "متن اصلی", "wire", "⚡")
        items, pages = await service.list_buttons()
        assert pages == 1
        assert len(items) == 1
        item = items[0]
        assert item.callback_data == "test:callback"
        await service.set_button_text(item.id, "متن جدید")
        changed = await service.get_button(item.id)
        assert changed is not None
        assert changed.display_text == "متن جدید"
        assert changed.callback_data == "test:callback"

    asyncio.run(scenario())


def test_message_override_updates_runtime_and_can_reset(tmp_path):
    service = PremiumUIService(str(tmp_path / "pui.db"))
    key = "public_order_registered"
    original = config.MESSAGES[key]

    async def scenario():
        await service.ensure_schema()
        await service.set_message(key, "{emoji:success} سفارش ثبت شد")
        assert config.MESSAGES[key] == "{emoji:success} سفارش ثبت شد"
        async with aiosqlite.connect(service.db_path) as conn:
            async with conn.execute(
                "SELECT body FROM styled_message_overrides WHERE message_key=?", (key,)
            ) as cur:
                row = await cur.fetchone()
        assert row and row[0] == "{emoji:success} سفارش ثبت شد"
        await service.reset_message(key)
        assert config.MESSAGES[key] == service._base_messages[key]

    try:
        asyncio.run(scenario())
    finally:
        config.MESSAGES[key] = original
