from premium_ui_service import ButtonCatalogItem
from handlers.premium_ui_clean_buttons import _is_dynamic_noise, _sort_key


def _item(item_id: int, callback: str, text: str) -> ButtonCatalogItem:
    return ButtonCatalogItem(
        id=item_id,
        callback_data=callback,
        default_text=text,
        default_icon_key=None,
        default_fallback=None,
        display_text=None,
        emoji_key=None,
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
