import asyncio

from aiogram.types import InlineKeyboardButton

from premium_ui_service import ButtonCatalogItem, premium_ui_service
from handlers.premium_ui_clean_buttons import (
    _canonical_label,
    _coalesce_logical_duplicates,
    _is_dynamic_noise,
    _sort_key,
)


def _item(
    item_id: int,
    callback: str,
    text: str,
    *,
    display_text: str | None = None,
    emoji_key: str | None = None,
) -> ButtonCatalogItem:
    return ButtonCatalogItem(
        id=item_id,
        callback_data=callback,
        default_text=text,
        default_icon_key=None,
        default_fallback=None,
        display_text=display_text,
        emoji_key=emoji_key,
    )


def test_internal_editor_buttons_are_hidden():
    assert _is_dynamic_noise(_item(1, "pui:b:4", "بازگشت"))
    assert _is_dynamic_noise(_item(2, "puc:bs:1", "بعدی"))


def test_numeric_entity_rows_are_hidden_but_users_main_button_is_kept():
    assert _is_dynamic_noise(_item(1, "cc:user:356770827", "👤 356770827"))
    assert not _is_dynamic_noise(_item(2, "cc:users:0", "کاربران"))


def test_main_dashboard_buttons_sort_before_generic_buttons():
    main_button = _item(10, "sudo_menu_panels", "مرکز پنل‌ها")
    generic = _item(999, "some:other", "یک دکمه")
    assert _sort_key(main_button) < _sort_key(generic)


def test_canonical_label_collapses_legacy_leading_emoji():
    assert _canonical_label("🧩 مرکز پنل‌ها") == "مرکز پنل‌ها"
    assert _canonical_label("مرکز پنل‌ها") == "مرکز پنل‌ها"
    assert _canonical_label("💵 مالی و پرداخت") == "مالی و پرداخت"


def test_duplicate_variants_collapse_and_existing_emoji_is_mirrored(monkeypatch):
    saved = []

    async def fake_save(item, display_text, emoji_key):
        saved.append((item.id, display_text, emoji_key))

    monkeypatch.setattr(premium_ui_service, "_save_button_override", fake_save)
    items = [
        _item(10, "sudo_menu_panels", "مرکز پنل‌ها", emoji_key="panel"),
        _item(11, "sudo_menu_panels", "🧩 مرکز پنل‌ها"),
    ]
    result = asyncio.run(_coalesce_logical_duplicates(items))

    assert len(result) == 1
    assert result[0].default_text == "مرکز پنل‌ها"
    assert result[0].emoji_key == "panel"
    assert (11, None, "panel") in saved


def test_aiogram_supports_premium_emoji_icon_on_inline_buttons():
    assert "icon_custom_emoji_id" in InlineKeyboardButton.model_fields
