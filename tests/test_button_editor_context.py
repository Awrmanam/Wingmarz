from pathlib import Path
from types import SimpleNamespace

from handlers.button_editor_context import trial_button_context


def item(callback, text, display=None):
    return SimpleNamespace(callback_data=callback, default_text=text, display_text=display)


def test_main_trial_button_is_unambiguous_and_first():
    result = trial_button_context(item("svcmarket:trial", "🧪 تست رایگان"))
    assert result == (0, "منوی اصلی → تست رایگان", "تست رایگان")


def test_customer_trial_buttons_have_screen_context():
    assert trial_button_context(item("trialv2:choose:config", "کانفیگ تست"))[1] == "صفحه تست رایگان → کانفیگ تست"
    assert trial_button_context(item("trialv2:choose:panel", "پنل نمایندگی تست"))[1] == "صفحه تست رایگان → پنل نمایندگی تست"
    assert trial_button_context(item("trialv2:root", "بازگشت"))[1] == "بازگشت → صفحه تست رایگان"


def test_admin_trial_settings_are_not_mixed_with_customer_buttons():
    assert trial_button_context(item("ops:trial:cooldown", "فاصله دریافت")) is None
    assert trial_button_context(item("ops:trial:traffic", "تنظیم حجم")) is None
    assert trial_button_context(item("ux:trialadmin:plans", "سرویس‌های قابل تست")) is None
    assert trial_button_context(item("ux:paneltrial:set:users", "حد کاربر")) is None


def test_context_router_precedes_generic_button_category_handler():
    source = Path("handlers/__init__.py").read_text(encoding="utf-8")
    assert source.index("include_router(button_editor_context_router)") < source.index("include_router(ui_editor_v2_router)")
