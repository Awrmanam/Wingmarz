import asyncio

from handlers import dashboard_label_fix
from handlers import operations


def test_menu_label_uses_persian_default_when_style_has_no_alias(monkeypatch):
    async def no_alias(scope, raw_identity):
        assert scope == "menu"
        return raw_identity, raw_identity

    monkeypatch.setattr(dashboard_label_fix.style_engine, "resolve_visual_alias", no_alias)
    value = asyncio.run(operations._menu_label("sudo_menu_sales", "فروش و تعرفه‌ها"))
    assert value == "فروش و تعرفه‌ها"


def test_menu_label_keeps_real_visual_override(monkeypatch):
    async def with_alias(scope, raw_identity):
        return raw_identity, "فروش ویژه"

    monkeypatch.setattr(dashboard_label_fix.style_engine, "resolve_visual_alias", with_alias)
    value = asyncio.run(operations._menu_label("sudo_menu_sales", "فروش و تعرفه‌ها"))
    assert value == "فروش ویژه"


def test_menu_label_falls_back_if_style_resolution_fails(monkeypatch):
    async def broken_alias(scope, raw_identity):
        raise RuntimeError("cache unavailable")

    monkeypatch.setattr(dashboard_label_fix.style_engine, "resolve_visual_alias", broken_alias)
    value = asyncio.run(operations._menu_label("cc:test", "کانفیگ تست"))
    assert value == "کانفیگ تست"
