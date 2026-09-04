import asyncio

import aiosqlite

import config
from handlers import control_center as cc
from style_engine import StyleEngine


def run(coro):
    return asyncio.run(coro)


def test_control_center_has_requested_sections(tmp_path, monkeypatch):
    db_path = str(tmp_path / "center.db")
    monkeypatch.setattr(config, "DATABASE_PATH", db_path)
    engine = StyleEngine(db_path)
    run(engine.init())
    monkeypatch.setattr(cc, "style_engine", engine)

    keyboard = run(cc.build_control_center_keyboard())
    labels = [button.text for row in keyboard.inline_keyboard for button in row]
    callbacks = [button.callback_data for row in keyboard.inline_keyboard for button in row]

    expected_labels = [
        "فروش و تعرفه‌ها",
        "مرکز پنل‌ها",
        "سفارش‌ها",
        "دسته‌بندی پلن‌ها",
        "کاربران",
        "مالی و پرداخت",
        "تخفیف‌ها",
        "کانفیگ تست",
        "ادمین‌های ربات",
        "آمار و گزارشات",
        "اطلاع‌رسانی",
        "پشتیبانی و تیکت",
        "ایموجی و استایل",
        "مدیریت متن‌ها",
        "دکمه‌ها و منوها",
        "ابزارها و بکاپ",
        "تنظیمات",
    ]
    for label in expected_labels:
        assert any(label in value for value in labels)

    assert "sudo_menu_sales" in callbacks
    assert "sudo_menu_panels" in callbacks
    assert "cc:orders:0" in callbacks
    assert "cc:users:0" in callbacks
    assert "cc:stats" in callbacks
    assert "cc:tickets:0" in callbacks
    assert "style:menu" in callbacks
    assert "sudo_menu_backup" in callbacks
    assert "sudo_menu_settings" in callbacks


def test_ticket_schema_is_migration_safe(tmp_path, monkeypatch):
    db_path = str(tmp_path / "tickets.db")
    monkeypatch.setattr(config, "DATABASE_PATH", db_path)
    run(cc._ensure_schema())
    run(cc._ensure_schema())

    async def inspect():
        async with aiosqlite.connect(db_path) as conn:
            async with conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='support_tickets'"
            ) as cur:
                assert await cur.fetchone() is not None
            async with conn.execute("PRAGMA foreign_key_list(support_tickets)") as cur:
                assert await cur.fetchall() == []

    run(inspect())


def test_sudo_authorization_is_config_backed(monkeypatch):
    monkeypatch.setattr(config, "SUDO_ADMINS", [101, 202])
    assert cc._is_sudo(101) is True
    assert cc._is_sudo(999) is False


def test_page_size_is_bounded():
    assert 1 <= cc.PAGE_SIZE <= 20
