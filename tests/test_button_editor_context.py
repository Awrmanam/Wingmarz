from pathlib import Path

from ui_presentation_registry import resolve_button, screen_for


def resolved(callback, text="دکمه"):
    return resolve_button(callback, text).button


def test_customer_home_buttons_map_to_real_screens():
    assert resolved("public_buy_reseller", "خرید پنل نمایندگی").screen == "public.home"
    assert resolved("svcmarket:trial", "تست رایگان").screen == "home.shared"
    assert resolved("support:home", "پشتیبانی").screen == "home.shared"
    assert resolved("my_info", "اطلاعات من").screen == "reseller.home"
    assert resolved("admin_renew", "تمدید/افزایش").screen == "reseller.home"


def test_trial_editor_contains_only_canonical_customer_controls():
    assert resolved("trialv2:choose:config", "کانفیگ تست").screen == "trial.home"
    assert resolved("trialv2:choose:panel", "پنل نمایندگی تست").screen == "trial.home"
    assert resolve_button("svcmarket:trial:config", "کانفیگ تست").button is None
    assert resolve_button("ops:trial:request", "تست رایگان").button is None
    assert resolve_button("ops:trial:request", "تست رایگان").excluded_reason


def test_dynamic_business_records_are_not_editable_buttons():
    for callback, text in [
        ("trialv2:cfg:12", "WireGuard"),
        ("trialv2:panel:12", "WireGuard"),
        ("planmarket:c:p:5", "WireGuard · 3 پلن"),
        ("planmarket:p:p:5:14:30d", "اقتصادی · 180,000 ت"),
        ("cc:user:356770827", "356770827"),
        ("cc:order:91", "اقتصادی · در انتظار"),
        ("ops:disc:item:4", "RETURN10"),
        ("order_approve_91", "تأیید و صدور"),
    ]:
        result = resolve_button(callback, text)
        assert result.button is None
        assert result.excluded_reason


def test_internal_and_legacy_editor_controls_are_hidden():
    for callback in ["pui:b:12", "uiv2:bc:trial:0", "uiv3:scope:customer", "uiv4:scope:customer", "style:emojis"]:
        result = resolve_button(callback, "دکمه")
        assert result.button is None
        assert result.excluded_reason


def test_duplicate_dashboard_callback_is_disambiguated_by_visible_identity():
    sales = resolved("sudo_menu_sales", "🛒 فروش و تعرفه‌ها")
    finance = resolved("sudo_menu_sales", "💵 مالی و پرداخت")
    assert sales and finance
    assert sales.key != finance.key
    assert sales.title == "فروش و تعرفه‌ها"
    assert finance.title == "مالی و پرداخت"


def test_explicit_dashboard_style_button_survives_internal_style_filter():
    item = resolved("style:menu", "ایموجی و استایل")
    assert item and item.screen == "admin.dashboard"


def test_screen_titles_are_human_readable_not_technical_routes():
    for key in ["public.home", "trial.home", "admin.dashboard", "admin.products", "admin.settings"]:
        title = screen_for(key).title.lower()
        assert not any(word in title for word in ("callback", "handler", "legacy", "fsm", "route"))


def test_context_router_precedes_obsolete_editor():
    source = Path("handlers/__init__.py").read_text(encoding="utf-8")
    assert source.index("include_router(button_editor_context_router)") < source.index("include_router(ui_editor_v2_router)")


def test_editor_is_registry_driven_and_has_screen_level_navigation():
    source = Path("handlers/button_editor_context.py").read_text(encoding="utf-8")
    assert "resolve_button" in source
    assert "uiv4:screen:" in source
    assert "ButtonContext" not in source
    assert "مسیر سازگاری" not in source
    assert "دسترسی کاربر" not in source
