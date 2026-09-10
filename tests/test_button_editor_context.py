from pathlib import Path
from types import SimpleNamespace

from handlers.button_editor_context import ButtonContext, button_context


def item(callback, text="دکمه", display=None):
    return SimpleNamespace(callback_data=callback, default_text=text, display_text=display)


def ctx(callback, text="دکمه") -> ButtonContext | None:
    return button_context(item(callback, text))


def test_customer_main_and_reseller_buttons_are_contextualized():
    assert ctx("public_buy_reseller", "🛒 خرید پنل نمایندگی") == ButtonContext(
        "customer", "sales", "منوی مشتری → خرید پنل", 0
    )
    assert ctx("admin_buy_reseller", "🛒 خرید پنل نمایندگی").context == "منوی نماینده → خرید پنل"
    assert ctx("my_info", "اطلاعات من").category == "account"
    assert ctx("admin_renew", "تمدید/افزایش").category == "account"


def test_trial_customer_and_trial_admin_are_separate_everywhere():
    public_trial = ctx("svcmarket:trial", "🧪 تست رایگان")
    config_trial = ctx("trialv2:choose:config", "کانفیگ تست")
    admin_cooldown = ctx("ops:trial:cooldown", "فاصله دریافت")
    admin_services = ctx("ux:trialadmin:plans", "پنل‌های قابل تست")

    assert public_trial.scope == "customer" and public_trial.category == "trial"
    assert config_trial.scope == "customer" and config_trial.category == "trial"
    assert admin_cooldown.scope == "manage" and admin_cooldown.category == "trial_admin"
    assert admin_services.scope == "manage" and admin_services.category == "trial_admin"


def test_management_dashboard_is_not_mixed_with_customer_ui():
    assert ctx("cc:orders:0", "سفارش‌ها").scope == "manage"
    assert ctx("cc:buttons", "دکمه‌ها و منوها").category == "content"
    assert ctx("sudo_menu_backup", "ابزارها و بکاپ").category == "tools"
    assert ctx("sudo_menu_settings", "تنظیمات").category == "settings"


def test_support_is_split_by_real_audience():
    assert ctx("support:home", "پشتیبانی").scope == "customer"
    assert ctx("support:new", "ایجاد تیکت").category == "support"
    assert ctx("cc:tickets:0", "پشتیبانی و تیکت").scope == "manage"


def test_dynamic_business_rows_never_pollute_button_editor():
    assert ctx("trialv2:cfg:12", "WireGuard") is None
    assert ctx("trialv2:panel:12", "WireGuard") is None
    assert ctx("planmarket:c:p:5", "WireGuard · 3 پلن") is None
    assert ctx("planmarket:p:p:5:14:30d", "اقتصادی · 180,000 ت") is None
    assert ctx("cc:user:356770827", "356770827") is None
    assert ctx("cc:order:91", "اقتصادی · در انتظار") is None
    assert ctx("ops:disc:item:4", "RETURN10") is None


def test_order_action_instances_are_not_listed_as_hundreds_of_buttons():
    assert ctx("order_approve_91", "تأیید و صدور") is None
    assert ctx("order_reject_91", "رد سفارش") is None
    assert ctx("order_retry_91", "بررسی و تلاش دوباره") is None


def test_internal_editor_controls_are_never_editable():
    assert ctx("pui:b:12", "تغییر متن") is None
    assert ctx("uiv2:bc:trial:0", "تست رایگان") is None
    assert ctx("uiv3:scope:customer", "کاربر و نماینده") is None


def test_unknown_static_button_goes_to_separate_bucket_not_wrong_feature():
    result = ctx("future_feature_home", "قابلیت آینده")
    assert result == ButtonContext("manage", "other", "سایر دکمه‌های ثابت", 90)


def test_context_router_precedes_obsolete_substring_editor():
    source = Path("handlers/__init__.py").read_text(encoding="utf-8")
    assert source.index("include_router(button_editor_context_router)") < source.index("include_router(ui_editor_v2_router)")


def test_legacy_trial_only_logic_is_gone():
    source = Path("handlers/button_editor_context.py").read_text(encoding="utf-8")
    assert "trial_button_context" not in source
    assert "legacy_category_redirect" in source
    assert 'F.data == "cc:buttons"' in source
