import asyncio
from types import SimpleNamespace

import aiosqlite
import pytest

import config
from handlers import style_admin as style_admin_module
from style_engine import (
    StyleEngine,
    StyleValidationError,
    extract_single_custom_emoji_id,
)


def run(coro):
    return asyncio.run(coro)


def fake_entity(entity_type="custom_emoji", custom_emoji_id="5368324170671202286"):
    return SimpleNamespace(type=entity_type, custom_emoji_id=custom_emoji_id)


def fake_message(*, entities=None, caption_entities=None):
    return SimpleNamespace(entities=entities, caption_entities=caption_entities)


def make_engine(tmp_path):
    return StyleEngine(str(tmp_path / "style.db"))


def test_extracts_one_custom_emoji_from_entities():
    message = fake_message(entities=[fake_entity()])
    assert extract_single_custom_emoji_id(message) == "5368324170671202286"


def test_extracts_one_custom_emoji_from_caption_entities():
    message = fake_message(caption_entities=[fake_entity(custom_emoji_id="123456789")])
    assert extract_single_custom_emoji_id(message) == "123456789"


def test_rejects_message_without_custom_emoji():
    message = fake_message(entities=[fake_entity(entity_type="bold")])
    with pytest.raises(StyleValidationError):
        extract_single_custom_emoji_id(message)


def test_rejects_multiple_custom_emojis():
    message = fake_message(entities=[fake_entity(custom_emoji_id="1"), fake_entity(custom_emoji_id="2")])
    with pytest.raises(StyleValidationError):
        extract_single_custom_emoji_id(message)


def test_rejects_custom_emoji_entity_without_id():
    message = fake_message(entities=[fake_entity(custom_emoji_id=None)])
    with pytest.raises(StyleValidationError):
        extract_single_custom_emoji_id(message)


@pytest.mark.parametrize("value", ["", "abc", "12x", "-1", "1.2", " ", None])
def test_invalid_custom_emoji_ids_are_rejected(value):
    with pytest.raises(StyleValidationError):
        StyleEngine.validate_custom_emoji_id(value)


@pytest.mark.parametrize("key", ["UPPER", "bad key", "x/y", "<tag>", "", "a" * 65])
def test_invalid_style_keys_are_rejected(key):
    with pytest.raises(StyleValidationError):
        StyleEngine.validate_key(key)


def test_schema_is_migration_safe_and_has_no_business_foreign_keys(tmp_path):
    engine = make_engine(tmp_path)
    run(engine.ensure_schema())

    async def inspect():
        async with aiosqlite.connect(engine.db_path) as conn:
            tables = {}
            for table in ("styled_emojis", "styled_settings", "styled_text_overrides"):
                async with conn.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table}'") as cur:
                    tables[table] = await cur.fetchone()
            fks = {}
            for table in ("styled_emojis", "styled_settings", "styled_text_overrides"):
                async with conn.execute(f"PRAGMA foreign_key_list({table})") as cur:
                    fks[table] = await cur.fetchall()
            return tables, fks

    tables, fks = run(inspect())
    assert all(tables.values())
    assert fks == {"styled_emojis": [], "styled_settings": [], "styled_text_overrides": []}


def test_style_defaults_to_disabled(tmp_path):
    engine = make_engine(tmp_path)
    assert run(engine.is_enabled()) is False


def test_add_and_get_emoji_mapping(tmp_path):
    engine = make_engine(tmp_path)
    item = run(engine.upsert_emoji("home", "123456", "🏠"))
    assert item.id > 0
    loaded = run(engine.get_emoji("home"))
    assert loaded.custom_emoji_id == "123456"
    assert loaded.fallback_unicode == "🏠"


def test_replace_mapping_keeps_single_logical_key(tmp_path):
    engine = make_engine(tmp_path)
    first = run(engine.upsert_emoji("home", "111", "🏠"))
    second = run(engine.upsert_emoji("home", "222", "⌂"))
    assert first.id == second.id
    assert second.custom_emoji_id == "222"
    assert len(run(engine.list_emojis())) == 1


def test_remove_mapping(tmp_path):
    engine = make_engine(tmp_path)
    item = run(engine.upsert_emoji("home", "111", "🏠"))
    assert run(engine.remove_emoji(item.id)) is True
    assert run(engine.get_emoji("home")) is None


def test_update_unicode_fallback_invalidates_cache(tmp_path):
    engine = make_engine(tmp_path)
    item = run(engine.upsert_emoji("home", "111", "🏠"))
    assert run(engine.update_fallback(item.id, "⌂")) is True
    assert run(engine.get_emoji("home")).fallback_unicode == "⌂"


def test_style_disabled_returns_unicode_fallback_only(tmp_path):
    engine = make_engine(tmp_path)
    run(engine.upsert_emoji("home", "111", "🏠"))
    assert run(engine.render_emoji("home")) == "🏠"


def test_missing_mapping_uses_explicit_unicode_fallback(tmp_path):
    engine = make_engine(tmp_path)
    assert run(engine.render_emoji("missing", "•")) == "•"


def test_disabled_mapping_uses_fallback_even_when_global_style_enabled(tmp_path):
    engine = make_engine(tmp_path)
    item = run(engine.upsert_emoji("home", "111", "🏠"))
    run(engine.set_enabled(True))
    run(engine.set_emoji_enabled(item.id, False))
    assert run(engine.render_emoji("home")) == "🏠"


def test_enabled_mapping_renders_telegram_custom_emoji_html(tmp_path):
    engine = make_engine(tmp_path)
    run(engine.upsert_emoji("home", "5368324170671202286", "🏠"))
    run(engine.set_enabled(True))
    assert run(engine.render_emoji("home")) == '<tg-emoji emoji-id="5368324170671202286">🏠</tg-emoji>'


def test_decorate_text_html_escapes_dynamic_text(tmp_path):
    engine = make_engine(tmp_path)
    result = run(engine.decorate_text("warning", '<b onclick="x">unsafe</b>', fallback="⚠️"))
    assert "<b onclick" not in result
    assert "&lt;b onclick=&quot;x&quot;&gt;unsafe&lt;/b&gt;" in result


def test_text_override_exact_match_preserves_raw_identity(tmp_path):
    engine = make_engine(tmp_path)
    run(engine.set_text_override("panel", "ProMax", "ProMax Premium"))
    raw, visible = run(engine.resolve_visual_alias("panel", "ProMax"))
    assert raw == "ProMax"
    assert visible == "ProMax Premium"


def test_text_override_does_not_fuzzy_match(tmp_path):
    engine = make_engine(tmp_path)
    run(engine.set_text_override("panel", "ProMax", "Premium"))
    assert run(engine.resolve_visual_alias("panel", "promax")) == ("promax", "promax")
    assert run(engine.resolve_visual_alias("panel", "ProMax-2")) == ("ProMax-2", "ProMax-2")


def test_remove_text_override(tmp_path):
    engine = make_engine(tmp_path)
    item = run(engine.set_text_override("plan", "4", "Gold Plan"))
    assert run(engine.remove_text_override(item.id)) is True
    assert run(engine.resolve_visual_alias("plan", "4")) == ("4", "4")


def test_cache_reload_reflects_external_database_change(tmp_path):
    engine = make_engine(tmp_path)
    run(engine.upsert_emoji("home", "111", "🏠"))

    async def mutate():
        async with aiosqlite.connect(engine.db_path) as conn:
            await conn.execute("UPDATE styled_emojis SET fallback_unicode='⌂' WHERE key='home'")
            await conn.commit()

    run(mutate())
    assert run(engine.get_emoji("home")).fallback_unicode == "🏠"
    engine.invalidate_cache()
    assert run(engine.get_emoji("home")).fallback_unicode == "⌂"


def test_styled_button_keeps_callback_data_identical_styles_off_and_on(tmp_path):
    engine = make_engine(tmp_path)
    run(engine.upsert_emoji("home", "111", "🏠"))
    off = run(engine.styled_button("خانه", icon_key="home", callback_data="panel:17"))
    run(engine.set_enabled(True))
    on = run(engine.styled_button("خانه", icon_key="home", callback_data="panel:17"))
    assert off.callback_data == "panel:17"
    assert on.callback_data == "panel:17"


def test_styled_button_keeps_non_callback_action_properties(tmp_path):
    engine = make_engine(tmp_path)
    button = run(engine.styled_button("سایت", fallback="🌐", url="https://example.com"))
    assert button.url == "https://example.com"
    assert button.callback_data is None


def test_inline_custom_icon_support_is_feature_detected_not_assumed():
    assert isinstance(StyleEngine.inline_button_supports_custom_icon(), bool)


def test_fallback_is_used_when_native_button_icon_is_unsupported(tmp_path, monkeypatch):
    engine = make_engine(tmp_path)
    run(engine.upsert_emoji("home", "111", "🏠"))
    run(engine.set_enabled(True))
    monkeypatch.setattr(StyleEngine, "inline_button_supports_custom_icon", staticmethod(lambda: False))
    button = run(engine.styled_button("خانه", icon_key="home", callback_data="home"))
    assert button.text.startswith("🏠")
    assert button.callback_data == "home"


def test_sudo_authorization_check_uses_only_sudo_admins(monkeypatch):
    monkeypatch.setattr(config, "SUDO_ADMINS", [1001, 1002])
    assert style_admin_module._authorized(1001) is True
    assert style_admin_module._authorized(2000) is False


def test_global_setting_persists_across_engine_instances(tmp_path):
    path = str(tmp_path / "style.db")
    first = StyleEngine(path)
    run(first.set_enabled(True))
    second = StyleEngine(path)
    assert run(second.is_enabled()) is True
