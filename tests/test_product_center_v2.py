import asyncio

import aiosqlite

from database import db
from product_catalog import ProductCatalogService
from service_marketplace_service import ServiceMarketplaceService


async def _schema(path: str):
    async with aiosqlite.connect(path) as conn:
        await conn.executescript(
            """
            CREATE TABLE rebecca_services (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                rebecca_service_id INTEGER NOT NULL UNIQUE,
                display_name TEXT NOT NULL,
                provider_name TEXT,
                source_username TEXT,
                is_enabled INTEGER NOT NULL DEFAULT 1,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE plans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                plan_type TEXT DEFAULT 'both',
                traffic_limit_bytes INTEGER,
                time_limit_seconds INTEGER,
                max_users INTEGER,
                price INTEGER DEFAULT 0,
                is_active BOOLEAN DEFAULT 1,
                allow_incremental_renewal BOOLEAN DEFAULT 1,
                rebecca_service_ids TEXT NULL
            );
            CREATE TABLE orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                plan_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending'
            );
            CREATE TABLE admins (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                origin_plan_id INTEGER
            );
            """
        )
        await conn.execute(
            "INSERT INTO rebecca_services(rebecca_service_id,display_name,provider_name,is_enabled) VALUES(7,'Wire Panel','Wire',1)"
        )
        await conn.execute(
            """
            INSERT INTO plans(name,traffic_limit_bytes,time_limit_seconds,max_users,price,is_active,rebecca_service_ids)
            VALUES('اقتصادی',107374182400,2592000,30,180000,1,'7')
            """
        )
        await conn.commit()


def test_owner_defined_panel_name_is_canonical(tmp_path):
    path = str(tmp_path / "catalog.db")
    asyncio.run(_schema(path))
    service = ProductCatalogService(path)
    old_path = db.db_path
    db.db_path = path
    try:
        async def scenario():
            categories = await service.categories(active_only=True, sellable_only=True)
            assert len(categories) == 1
            assert categories[0].name == "Wire Panel"
            assert categories[0].provider_name == "Wire"
            plans = await service.plans_for_category(categories[0].id)
            assert [p.name for p in plans] == ["اقتصادی"]
        asyncio.run(scenario())
    finally:
        db.db_path = old_path


def test_category_rename_updates_canonical_rebecca_display_name(tmp_path):
    path = str(tmp_path / "rename.db")
    asyncio.run(_schema(path))
    service = ProductCatalogService(path)
    old_path = db.db_path
    db.db_path = path
    try:
        async def scenario():
            category = (await service.categories())[0]
            await service.update_category(category.id, name="WireGuard VIP")
            async with aiosqlite.connect(path) as conn:
                async with conn.execute("SELECT display_name FROM rebecca_services WHERE rebecca_service_id=7") as cur:
                    assert (await cur.fetchone())[0] == "WireGuard VIP"
            refreshed = await service.get_category(category.id)
            assert refreshed.name == "WireGuard VIP"
        asyncio.run(scenario())
    finally:
        db.db_path = old_path


def test_automatic_duration_labels_keep_30_days_as_one_month():
    assert ServiceMarketplaceService.duration_label(30 * 86400) == "1 ماهه"
    assert ServiceMarketplaceService.duration_label(90 * 86400) == "3 ماهه"


def test_plan_fields_and_description_are_editable(tmp_path):
    path = str(tmp_path / "edit.db")
    asyncio.run(_schema(path))
    service = ProductCatalogService(path)
    old_path = db.db_path
    db.db_path = path
    try:
        async def scenario():
            category = (await service.categories())[0]
            plan = (await service.plans_for_category(category.id, only_active=False))[0]
            await service.update_category(category.id, name="WireGuard Pro", description="سرویس مخصوص بازی")
            await service.update_plan_field(plan.id, "name", "حرفه‌ای")
            await service.update_plan_field(plan.id, "price", 250000)
            await service.update_plan_field(plan.id, "max_users", 50)
            await service.update_plan_field(plan.id, "time_limit_seconds", 90 * 86400)
            await service.set_plan_description(plan.id, "پلن مناسب مصرف بالا")
            updated = await db.get_plan_by_id(plan.id)
            assert updated.name == "حرفه‌ای"
            assert updated.price == 250000
            assert updated.max_users == 50
            assert updated.time_limit_seconds == 90 * 86400
            assert await service.plan_description(plan.id) == "پلن مناسب مصرف بالا"
            category2 = await service.get_category(category.id)
            assert category2.name == "WireGuard Pro"
            assert category2.description == "سرویس مخصوص بازی"
        asyncio.run(scenario())
    finally:
        db.db_path = old_path


def test_safe_delete_preserves_historical_orders(tmp_path):
    path = str(tmp_path / "delete.db")
    asyncio.run(_schema(path))
    service = ProductCatalogService(path)
    old_path = db.db_path
    db.db_path = path
    try:
        async def scenario():
            category = (await service.categories())[0]
            plan = (await service.plans_for_category(category.id, only_active=False))[0]
            async with aiosqlite.connect(path) as conn:
                await conn.execute("INSERT INTO orders(user_id,plan_id,status) VALUES(1,?,'completed')", (plan.id,))
                await conn.commit()
            assert await service.safe_delete_plan(plan.id) == "archived"
            kept = await db.get_plan_by_id(plan.id)
            assert kept is not None
            assert kept.is_active is False

            new_id = await service.create_plan(
                category_id=category.id,
                name="موقت",
                description="",
                price=1000,
                traffic_bytes=None,
                duration_seconds=30 * 86400,
                max_users=2,
            )
            assert await service.safe_delete_plan(new_id) == "deleted"
            assert await db.get_plan_by_id(new_id) is None
        asyncio.run(scenario())
    finally:
        db.db_path = old_path


def test_panel_icon_candidates_reuse_registered_owner_key():
    assert ProductCatalogService._emoji_key_candidates("WireGuard", "Wire")[:2] == ["wireguard", "wire"]
    assert "openvpn" in ProductCatalogService._emoji_key_candidates("OpenVPN", "Open")


def test_trial_picker_uses_canonical_panel_layer():
    source = open("handlers/trial_ui_v2.py", encoding="utf-8").read()
    assert "category.name" in source
    assert "service.display_name" not in source
    assert "resolve_panel_icon_key" in source
    assert "trialv2:cfg:" in source


def test_paid_storefront_is_panel_first_and_keeps_purchase_callbacks():
    source = open("handlers/plan_storefront.py", encoding="utf-8").read()
    assert "planmarket:c:" in source
    assert "پنل موردنظر را انتخاب کنید" in source
    assert "resolve_panel_icon_key" in source
    assert "admin_order_" in source
    assert "public_order_" in source
