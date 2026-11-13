"""Additive migration from obsolete menu aliases to canonical button overrides."""
import asyncio

import aiosqlite

from premium_ui_service import PremiumUIService
from style_engine import StyleEngine


def test_legacy_menu_alias_migrates_once_without_losing_rollback_data(tmp_path):
    path = str(tmp_path / "legacy_ui.sqlite")
    legacy = StyleEngine(path)
    modern = PremiumUIService(path)

    async def scenario():
        await legacy.set_text_override(
            "menu", "ops:hub:sales", "🛒 فروش سفارشی",
        )
        await modern.migrate_legacy_menu_aliases()
        await modern.reload_button_overrides()
        item = await modern.get_button_by_identity(
            "ops:hub:sales", "فروش و سفارش‌ها",
        )
        assert item is not None
        assert item.display_text == "🛒 فروش سفارشی"
        assert modern.override_for(
            "ops:hub:sales", "فروش و سفارش‌ها",
        ) == ("🛒 فروش سفارشی", None)

        # Operators can reset the new editor without reviving the old alias.
        await modern.reset_button_text(item.id)
        assert modern.override_for("ops:hub:sales", "فروش و سفارش‌ها") is None
        assert await modern.migrate_legacy_menu_aliases() == 0
        await modern.reload_button_overrides()
        assert modern.override_for("ops:hub:sales", "فروش و سفارش‌ها") is None

        async with aiosqlite.connect(path) as conn:
            async with conn.execute(
                "SELECT display_text FROM styled_text_overrides "
                "WHERE scope='menu' AND raw_identity='ops:hub:sales'"
            ) as cursor:
                assert (await cursor.fetchone())[0] == "🛒 فروش سفارشی"
            async with conn.execute(
                "SELECT COUNT(*) FROM ui_presentation_migration_meta "
                "WHERE key='dashboard_menu_aliases_v1'"
            ) as cursor:
                assert (await cursor.fetchone())[0] == 1

    asyncio.run(scenario())


def test_canonical_button_override_takes_precedence_over_legacy_alias(tmp_path):
    path = str(tmp_path / "legacy_precedence.sqlite")
    legacy = StyleEngine(path)
    modern = PremiumUIService(path)

    async def scenario():
        await legacy.set_text_override("menu", "ops:hub:finance", "مالی قدیمی")
        await modern.catalog_button("ops:hub:finance", "مالی و پرداخت‌ها", None, None)
        item = await modern.get_button_by_identity("ops:hub:finance", "مالی و پرداخت‌ها")
        assert item
        await modern.set_button_text(item.id, "پرداخت جدید")
        await modern.migrate_legacy_menu_aliases()
        await modern.reload_button_overrides()
        item = await modern.get_button_by_identity("ops:hub:finance", "مالی و پرداخت‌ها")
        assert item.display_text == "پرداخت جدید"

    asyncio.run(scenario())
