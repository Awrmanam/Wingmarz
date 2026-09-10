from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable


@dataclass(frozen=True)
class ScreenDef:
    key: str
    scope: str
    category: str
    title: str
    rank: int = 50


@dataclass(frozen=True)
class ButtonDef:
    key: str
    screen: str
    title: str
    callback_data: str
    default_text: str | None = None
    rank: int = 50
    navigation_type: str = "action"


@dataclass(frozen=True)
class ButtonResolution:
    button: ButtonDef | None
    excluded_reason: str | None = None


SCOPE_META = {
    "customer": ("کاربر و نماینده", "👤"),
    "manage": ("مدیریت ربات", "🛠"),
}

CATEGORY_META = {
    "main": ("صفحه اصلی و ناوبری", "🏠"),
    "sales": ("خرید و پلن‌ها", "🛒"),
    "trial": ("تست رایگان", "🧪"),
    "account": ("پنل و حساب", "🧩"),
    "payment": ("پرداخت و سفارش", "💳"),
    "support": ("پشتیبانی", "🎧"),
    "dashboard": ("مرکز مدیریت", "🧭"),
    "products": ("محصولات و پنل‌ها", "📦"),
    "orders": ("سفارش‌ها", "🧾"),
    "users": ("کاربران", "👥"),
    "finance": ("مالی و پرداخت", "💵"),
    "discounts": ("تخفیف‌ها", "🎟"),
    "trial_admin": ("تنظیمات تست", "🧪"),
    "support_admin": ("تیکت و پشتیبانی", "🎧"),
    "access": ("ادمین‌ها و دسترسی", "🔐"),
    "reports": ("آمار و گزارش", "📊"),
    "broadcast": ("اطلاع‌رسانی", "📢"),
    "content": ("متن، دکمه و ظاهر", "🎨"),
    "settings": ("تنظیمات", "⚙️"),
    "tools": ("ابزارها و بکاپ", "🧰"),
}


SCREENS: dict[str, ScreenDef] = {
    # Customer / reseller
    "public.home": ScreenDef("public.home", "customer", "main", "صفحه اصلی مشتری", 0),
    "reseller.home": ScreenDef("reseller.home", "customer", "main", "صفحه اصلی نماینده", 1),
    "home.shared": ScreenDef("home.shared", "customer", "main", "دکمه‌های مشترک صفحه اصلی", 2),
    "reseller.navigation": ScreenDef("reseller.navigation", "customer", "main", "بازگشت به صفحه اصلی نماینده", 90),
    "purchase.entry": ScreenDef("purchase.entry", "customer", "sales", "شروع خرید", 0),
    "purchase.duration": ScreenDef("purchase.duration", "customer", "sales", "انتخاب مدت", 1),
    "purchase.plan": ScreenDef("purchase.plan", "customer", "sales", "انتخاب پلن", 2),
    "purchase.summary": ScreenDef("purchase.summary", "customer", "sales", "خلاصه خرید", 3),
    "payment.checkout": ScreenDef("payment.checkout", "customer", "payment", "پرداخت و ثبت سفارش", 0),
    "trial.home": ScreenDef("trial.home", "customer", "trial", "صفحه تست رایگان", 0),
    "trial.config": ScreenDef("trial.config", "customer", "trial", "تست رایگان کانفیگ", 1),
    "trial.panel": ScreenDef("trial.panel", "customer", "trial", "تست پنل نمایندگی", 2),
    "reseller.account": ScreenDef("reseller.account", "customer", "account", "حساب نماینده", 0),
    "reseller.renew": ScreenDef("reseller.renew", "customer", "account", "تمدید و افزایش", 1),
    "reseller.cleanup": ScreenDef("reseller.cleanup", "customer", "account", "پاکسازی کاربران نماینده", 2),
    "support.home": ScreenDef("support.home", "customer", "support", "پشتیبانی", 0),

    # Management
    "admin.dashboard": ScreenDef("admin.dashboard", "manage", "dashboard", "مرکز مدیریت ربات", 0),
    "admin.navigation": ScreenDef("admin.navigation", "manage", "dashboard", "ناوبری مدیریت", 90),
    "admin.panels": ScreenDef("admin.panels", "manage", "products", "مرکز پنل‌ها", 0),
    "admin.panel_add": ScreenDef("admin.panel_add", "manage", "products", "افزودن پنل", 1),
    "admin.panel_import": ScreenDef("admin.panel_import", "manage", "products", "افزودن پنل قبلی", 2),
    "admin.panel_cleanup": ScreenDef("admin.panel_cleanup", "manage", "products", "پاکسازی و نگهداری پنل‌ها", 3),
    "admin.products": ScreenDef("admin.products", "manage", "products", "محصولات و پلن‌ها", 4),
    "admin.rebecca": ScreenDef("admin.rebecca", "manage", "products", "سرویس‌های Rebecca", 5),
    "admin.orders": ScreenDef("admin.orders", "manage", "orders", "سفارش‌ها", 0),
    "admin.users": ScreenDef("admin.users", "manage", "users", "کاربران", 0),
    "admin.finance": ScreenDef("admin.finance", "manage", "finance", "مالی و پرداخت", 0),
    "admin.discounts": ScreenDef("admin.discounts", "manage", "discounts", "مدیریت تخفیف‌ها", 0),
    "admin.trial": ScreenDef("admin.trial", "manage", "trial_admin", "تنظیمات تست رایگان", 0),
    "admin.panel_trial": ScreenDef("admin.panel_trial", "manage", "trial_admin", "تنظیمات تست پنل", 1),
    "admin.support": ScreenDef("admin.support", "manage", "support_admin", "پشتیبانی و تیکت", 0),
    "admin.access": ScreenDef("admin.access", "manage", "access", "ادمین‌ها و دسترسی", 0),
    "admin.reports": ScreenDef("admin.reports", "manage", "reports", "آمار و گزارشات", 0),
    "admin.broadcast": ScreenDef("admin.broadcast", "manage", "broadcast", "اطلاع‌رسانی", 0),
    "admin.content": ScreenDef("admin.content", "manage", "content", "متن، دکمه و استایل", 0),
    "admin.settings": ScreenDef("admin.settings", "manage", "settings", "تنظیمات", 0),
    "admin.tools": ScreenDef("admin.tools", "manage", "tools", "ابزارها و بکاپ", 0),
}


def _clean(value: str | None) -> str:
    text = str(value or "").strip()
    text = re.sub(r"^[^\w@]+", "", text, flags=re.UNICODE).strip()
    return re.sub(r"\s+", " ", text)


BUTTONS: tuple[ButtonDef, ...] = (
    # Customer/reseller home. Shared entries appear once because the current
    # persistence identity is callback_data + default_text, so one edit affects
    # the same visible control on both homes.
    ButtonDef("public.home.buy", "public.home", "خرید پنل نمایندگی", "public_buy_reseller", rank=0),
    ButtonDef("reseller.home.info", "reseller.home", "اطلاعات من", "my_info", rank=0),
    ButtonDef("reseller.home.report", "reseller.home", "گزارش من", "my_report", rank=1),
    ButtonDef("reseller.home.users", "reseller.home", "کاربران من", "my_users", rank=2),
    ButtonDef("reseller.home.reactivate", "reseller.home", "فعال‌سازی کاربران", "reactivate_users", rank=3),
    ButtonDef("reseller.home.buy", "reseller.home", "خرید پنل نمایندگی", "admin_buy_reseller", rank=4),
    ButtonDef("reseller.home.renew", "reseller.home", "تمدید/افزایش", "admin_renew", rank=5),
    ButtonDef("home.shared.trial", "home.shared", "تست رایگان", "svcmarket:trial", rank=0),
    ButtonDef("home.shared.support", "home.shared", "پشتیبانی", "support:home", rank=1),
    ButtonDef("reseller.navigation.back", "reseller.navigation", "بازگشت", "back_to_admin_main", rank=0, navigation_type="back"),

    # Trial customer UI. Old compatibility/access aliases are intentionally hidden.
    ButtonDef("trial.home.config", "trial.home", "کانفیگ تست", "trialv2:choose:config", rank=0),
    ButtonDef("trial.home.panel", "trial.home", "پنل نمایندگی تست", "trialv2:choose:panel", rank=1),
    ButtonDef("trial.home.back", "trial.home", "بازگشت", "svcmarket:home", rank=90, navigation_type="back"),
    ButtonDef("trial.config.back", "trial.config", "بازگشت", "trialv2:root", rank=90, navigation_type="back"),

    # Support
    ButtonDef("support.home.new", "support.home", "ایجاد تیکت", "support:new", rank=0),
    ButtonDef("support.home.back", "support.home", "بازگشت", "public_back_main", rank=90, navigation_type="back"),

    # Regular reseller cleanup confirmation screens.
    ButtonDef("reseller.cleanup.confirm_expired", "reseller.cleanup", "تأیید حذف کاربران منقضی", "global_cleanup_confirm", rank=0),
    ButtonDef("reseller.cleanup.confirm_quota", "reseller.cleanup", "تأیید حذف کاربران با سهمیه تمام‌شده", "global_small_quota_cleanup_confirm", rank=1),

    # Main management dashboard. Duplicate callback sudo_menu_sales is resolved
    # by the visible default text and stays two separate presentation identities.
    ButtonDef("admin.dashboard.sales", "admin.dashboard", "فروش و تعرفه‌ها", "sudo_menu_sales", "فروش و تعرفه‌ها", 0),
    ButtonDef("admin.dashboard.panels", "admin.dashboard", "مرکز پنل‌ها", "sudo_menu_panels", rank=1),
    ButtonDef("admin.dashboard.orders", "admin.dashboard", "سفارش‌ها", "cc:orders:0", rank=2),
    ButtonDef("admin.dashboard.products", "admin.dashboard", "محصولات و پلن‌ها", "sales_manage", rank=3),
    ButtonDef("admin.dashboard.users", "admin.dashboard", "کاربران", "cc:users:0", rank=4),
    ButtonDef("admin.dashboard.finance", "admin.dashboard", "مالی و پرداخت", "sudo_menu_sales", "مالی و پرداخت", 5),
    ButtonDef("admin.dashboard.discounts", "admin.dashboard", "تخفیف‌ها", "cc:discounts", rank=6),
    ButtonDef("admin.dashboard.trial", "admin.dashboard", "تست رایگان", "cc:test", rank=7),
    ButtonDef("admin.dashboard.admins", "admin.dashboard", "ادمین‌های ربات", "cc:botadmins", rank=8),
    ButtonDef("admin.dashboard.stats", "admin.dashboard", "آمار و گزارشات", "cc:stats", rank=9),
    ButtonDef("admin.dashboard.broadcast", "admin.dashboard", "اطلاع‌رسانی", "sudo_menu_broadcast", rank=10),
    ButtonDef("admin.dashboard.support", "admin.dashboard", "پشتیبانی و تیکت", "cc:tickets:0", rank=11),
    ButtonDef("admin.dashboard.style", "admin.dashboard", "ایموجی و استایل", "style:menu", rank=12),
    ButtonDef("admin.dashboard.texts", "admin.dashboard", "مدیریت متن‌ها", "cc:texts", rank=13),
    ButtonDef("admin.dashboard.buttons", "admin.dashboard", "دکمه‌ها و منوها", "cc:buttons", rank=14),
    ButtonDef("admin.dashboard.tools", "admin.dashboard", "ابزارها و بکاپ", "sudo_menu_backup", rank=15),
    ButtonDef("admin.dashboard.settings", "admin.dashboard", "تنظیمات", "sudo_menu_settings", rank=16),
    ButtonDef("admin.navigation.home", "admin.navigation", "خانه", "back_to_main", rank=0, navigation_type="home"),

    # Panel center and reseller provisioning wizards.
    ButtonDef("admin.panels.cleanup", "admin.panels", "پاکسازی", "sudo_menu_cleanup", rank=0),
    ButtonDef("admin.panels.reports", "admin.panels", "گزارشات", "sudo_menu_reports", rank=1),
    ButtonDef("admin.panels.add", "admin.panels", "افزودن ادمین", "add_admin", rank=2),
    ButtonDef("admin.panels.edit", "admin.panels", "ویرایش پنل", "edit_panel", rank=3),
    ButtonDef("admin.panels.activate", "admin.panels", "فعالسازی پنل", "activate_admin", rank=4),
    ButtonDef("admin.panels.manage", "admin.panels", "مدیریت ادمین‌ها", "sudo_manage_admins", rank=5),
    ButtonDef("admin.panels.auto_import", "admin.panels", "کشف خودکار ادمین", "auto_import_admins", rank=6),
    ButtonDef("admin.panels.import", "admin.panels", "افزودن ادمین قبلی", "import_admin", rank=7),
    ButtonDef("admin.panels.remove", "admin.panels", "حذف کامل پنل", "remove_admin", rank=8),
    ButtonDef("admin.panels.list", "admin.panels", "لیست ادمین‌ها", "list_admins", rank=9),
    ButtonDef("admin.panels.refresh_import", "admin.panel_import", "بروزرسانی لیست", "auto_import_refresh", rank=0),
    ButtonDef("admin.panels.cancel_import", "admin.panel_import", "لغو", "auto_import_cancel", rank=90, navigation_type="back"),
    ButtonDef("admin.panel_add.renew_inherit", "admin.panel_add", "تمدید مطابق پلن", "add_renew_mode_inherit", rank=10),
    ButtonDef("admin.panel_add.renew_inc", "admin.panel_add", "تمدید تدریجی مجاز", "add_renew_mode_inc", rank=11),
    ButtonDef("admin.panel_add.renew_full", "admin.panel_add", "فقط تمدید کامل", "add_renew_mode_full", rank=12),
    ButtonDef("admin.panel_add.confirm", "admin.panel_add", "تأیید و ایجاد", "confirm_create_admin", rank=20),
    ButtonDef("admin.panel_import.renew_inherit", "admin.panel_import", "تمدید مطابق پلن", "import_renew_mode_inherit", rank=10),
    ButtonDef("admin.panel_import.renew_inc", "admin.panel_import", "تمدید تدریجی مجاز", "import_renew_mode_inc", rank=11),
    ButtonDef("admin.panel_import.renew_full", "admin.panel_import", "فقط تمدید کامل", "import_renew_mode_full", rank=12),
    ButtonDef("admin.panel_import.confirm", "admin.panel_import", "تأیید و افزودن", "confirm_import_admin", rank=20),

    # Cleanup / maintenance
    ButtonDef("admin.cleanup.expired", "admin.panel_cleanup", "حذف منقضی‌های قدیمی", "sudo_cleanup_old_expired", rank=0),
    ButtonDef("admin.cleanup.quota", "admin.panel_cleanup", "حذف سهمیه‌های تمام‌شده", "sudo_cleanup_small_quota", rank=1),
    ButtonDef("admin.cleanup.reset", "admin.panel_cleanup", "ریست مصرف", "sudo_reset_usage", rank=2),
    ButtonDef("admin.cleanup.nonpayer", "admin.panel_cleanup", "ثبت عدم پرداخت", "sudo_non_payer", rank=3),

    # Product/plan center
    ButtonDef("admin.products.add_plan", "admin.products", "افزودن پلن", "sales_add", rank=0),
    ButtonDef("admin.products.services", "admin.products", "اتصال پلن به سرویس", "sales_edit_services", rank=1),
    ButtonDef("admin.products.rebecca", "admin.products", "سرویس‌های Rebecca", "rebecca_services", "سرویس‌های Rebecca", 2),
    ButtonDef("admin.products.rebecca_manage", "admin.products", "مدیریت سرویس‌های Rebecca", "rebecca_services", "مدیریت سرویس‌های Rebecca", 3),
    ButtonDef("admin.products.delete", "admin.products", "حذف پلن", "sales_delete", rank=4),
    ButtonDef("admin.products.back", "admin.products", "بازگشت", "pc:root", rank=90, navigation_type="back"),

    # Rebecca service catalog screens.
    ButtonDef("admin.rebecca.add", "admin.rebecca", "افزودن سرویس", "rsvc:add", rank=0),
    ButtonDef("admin.rebecca.list", "admin.rebecca", "لیست سرویس‌ها", "rebecca_services", "لیست سرویس‌ها", 1),
    ButtonDef("admin.rebecca.custom_name", "admin.rebecca", "نام دلخواه", "radd:custom", rank=10),
    ButtonDef("admin.rebecca.confirm_add", "admin.rebecca", "ثبت سرویس", "radd:confirm", rank=11),
    ButtonDef("admin.rebecca.cancel", "admin.rebecca", "لغو", "rsvc:cancel", rank=90, navigation_type="back"),
    ButtonDef("admin.rebecca.selection_done", "admin.rebecca", "تأیید انتخاب", "rcp:done", rank=20),
    ButtonDef("admin.rebecca.manual_service", "admin.rebecca", "ورود دستی شناسه سرویس", "rcp:manual", rank=21),

    # Finance/payment settings.
    ButtonDef("admin.finance.cards", "admin.finance", "مدیریت کارت‌ها", "sales_cards", rank=0),
    ButtonDef("admin.finance.billing", "admin.finance", "تعرفه تمدید", "set_billing", rank=1),
    ButtonDef("admin.finance.login_url", "admin.finance", "آدرس ورود", "set_login_url", rank=2),

    # Discounts.
    ButtonDef("admin.discounts.add", "admin.discounts", "ساخت کد تخفیف", "ops:disc:add", rank=0),
    ButtonDef("admin.discounts.percent", "admin.discounts", "درصدی", "ops:disc:type:percent", rank=10),
    ButtonDef("admin.discounts.fixed", "admin.discounts", "مبلغ ثابت", "ops:disc:type:fixed", rank=11),

    # Trial administration.
    ButtonDef("admin.trial.config", "admin.trial", "تنظیم کانفیگ تست", "ux:trialadmin:config", rank=0),
    ButtonDef("admin.trial.panel", "admin.trial", "تنظیم پنل تست", "ux:trialadmin:panel", rank=1),
    ButtonDef("admin.trial.services_allowed", "admin.trial", "پنل‌های قابل تست", "ux:trialadmin:plans", rank=2),
    ButtonDef("admin.trial.traffic", "admin.trial", "حجم تست", "ops:trial:traffic", rank=10),
    ButtonDef("admin.trial.duration", "admin.trial", "مدت تست", "ops:trial:duration", rank=11),
    ButtonDef("admin.trial.cooldown", "admin.trial", "فاصله دریافت تست", "ops:trial:cooldown", rank=12),
    ButtonDef("admin.trial.services", "admin.trial", "انتخاب سرویس Rebecca", "ops:trial:services", rank=13),
    ButtonDef("admin.trial.auto_service", "admin.trial", "انتخاب خودکار سرویس", "ops:trial:service:0", rank=14),
    ButtonDef("admin.trial.self", "admin.trial", "صدور تست برای خودم", "ops:trial:self", rank=15),
    ButtonDef("admin.panel_trial.traffic", "admin.panel_trial", "حجم", "ux:paneltrial:set:traffic", rank=0),
    ButtonDef("admin.panel_trial.duration", "admin.panel_trial", "اعتبار", "ux:paneltrial:set:duration", rank=1),
    ButtonDef("admin.panel_trial.users", "admin.panel_trial", "حد کاربر", "ux:paneltrial:set:users", rank=2),
    ButtonDef("admin.panel_trial.cooldown", "admin.panel_trial", "فاصله دریافت", "ux:paneltrial:set:cooldown", rank=3),

    # Tickets/support management.
    ButtonDef("admin.support.open", "admin.support", "تیکت‌های باز", "cc:tickets:open:0", rank=0),
    ButtonDef("admin.support.closed", "admin.support", "تیکت‌های بسته", "cc:tickets:closed:0", rank=1),

    # Access.
    ButtonDef("admin.access.add", "admin.access", "افزودن مدیر", "ops:ba:add", rank=0),

    # Reports and broadcast.
    ButtonDef("admin.reports.legacy", "admin.reports", "گزارشات تکمیلی", "sudo_menu_reports", "گزارشات قدیمی", 0),
    ButtonDef("admin.broadcast.all", "admin.broadcast", "ارسال به همه ادمین‌ها", "broadcast_all", rank=0),
    ButtonDef("admin.broadcast.active", "admin.broadcast", "ارسال به ادمین‌های فعال", "broadcast_active", rank=1),
    ButtonDef("admin.broadcast.confirm", "admin.broadcast", "تأیید ارسال", "broadcast_confirm", rank=20),

    # Settings.
    ButtonDef("admin.settings.forced_join", "admin.settings", "کانال‌های اجباری", "forced_join_manage", rank=0),

    # Backup/tools.
    ButtonDef("admin.tools.backup_now", "admin.tools", "بکاپ الان", "backup_now", rank=0),
    ButtonDef("admin.tools.backup_schedule", "admin.tools", "زمان‌بندی بکاپ", "backup_schedule", rank=1),
    ButtonDef("admin.tools.backup_restore", "admin.tools", "ریستور بکاپ", "backup_restore", rank=2),
)


_BY_CALLBACK: dict[str, list[ButtonDef]] = {}
for _button in BUTTONS:
    _BY_CALLBACK.setdefault(_button.callback_data, []).append(_button)


# Implementation-only controls are hidden from the normal editor. Explicit
# ButtonDefs are resolved before these rules, so e.g. style:menu can stay a real
# dashboard button while the internal style editor controls remain hidden.
_HIDDEN_PREFIX_REASONS: tuple[tuple[str, str], ...] = (
    ("pui:", "کنترل داخلی ویرایشگر"),
    ("puc:", "کنترل داخلی ویرایشگر"),
    ("uiv2:", "رابط قدیمی ویرایشگر"),
    ("uiv3:", "رابط قدیمی ویرایشگر"),
    ("uiv4:", "کنترل داخلی ویرایشگر"),
    ("style:", "کنترل داخلی استایل"),
    ("svcmarket:trial:config", "مسیر سازگاری قدیمی تست"),
    ("svcmarket:trial:panel", "مسیر سازگاری قدیمی تست"),
    ("ops:configtrial:request", "مسیر دسترسی داخلی تست"),
    ("ops:paneltrial:request", "مسیر دسترسی داخلی تست"),
    ("ops:trial:request", "مسیر دسترسی داخلی تست"),
)

_DYNAMIC_PREFIX_REASONS: tuple[tuple[str, str], ...] = (
    ("planmarket:c:", "نام پنل یا دسته پویا"),
    ("planmarket:d:", "دسته زمانی پویا"),
    ("planmarket:p:", "پلن پویا"),
    ("trialv2:cfg:", "پنل تست پویا"),
    ("trialv2:panel:", "پنل تست پویا"),
    ("cc:user:", "کاربر پویا"),
    ("cc:order:", "سفارش پویا"),
    ("cc:receipt:", "رسید پویا"),
    ("ops:disc:item:", "کد تخفیف پویا"),
    ("ops:disc:toggle:", "عملیات روی کد تخفیف پویا"),
    ("ops:disc:delete:", "عملیات روی کد تخفیف پویا"),
    ("ops:trial:service:", "سرویس تست پویا"),
    ("order_approve_", "عملیات روی سفارش پویا"),
    ("order_reject_", "عملیات روی سفارش پویا"),
    ("order_retry_", "عملیات روی سفارش پویا"),
)


def screen_for(key: str) -> ScreenDef | None:
    return SCREENS.get(key)


def screens_for(scope: str, category: str | None = None) -> list[ScreenDef]:
    rows = [s for s in SCREENS.values() if s.scope == scope and (category is None or s.category == category)]
    return sorted(rows, key=lambda s: (s.rank, s.title))


def categories_for(scope: str) -> list[str]:
    present = {s.category for s in SCREENS.values() if s.scope == scope}
    return [key for key in CATEGORY_META if key in present]


def _excluded(callback_data: str) -> str | None:
    for prefix, reason in _HIDDEN_PREFIX_REASONS:
        if callback_data.startswith(prefix):
            return reason
    for prefix, reason in _DYNAMIC_PREFIX_REASONS:
        if callback_data.startswith(prefix):
            return reason
    return None


def resolve_button(callback_data: str, default_text: str | None = None) -> ButtonResolution:
    callback = str(callback_data or "")
    if not callback:
        return ButtonResolution(None, "callback خالی")

    candidates = _BY_CALLBACK.get(callback, [])
    if candidates:
        if len(candidates) == 1:
            return ButtonResolution(candidates[0], None)
        wanted = _clean(default_text)
        for candidate in candidates:
            if candidate.default_text and _clean(candidate.default_text) == wanted:
                return ButtonResolution(candidate, None)
        return ButtonResolution(None, "دکمه نمایشی مشترک و مبهم")

    reason = _excluded(callback)
    if reason:
        return ButtonResolution(None, reason)
    return ButtonResolution(None, None)


def buttons_for_screen(screen_key: str) -> list[ButtonDef]:
    return sorted((b for b in BUTTONS if b.screen == screen_key), key=lambda b: (b.rank, b.title))


def is_explicitly_excluded(callback_data: str) -> bool:
    return _excluded(str(callback_data or "")) is not None


def all_registered_callbacks() -> set[str]:
    return set(_BY_CALLBACK)


def unresolved_static(callbacks: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
    missing: list[tuple[str, str]] = []
    for callback, text in callbacks:
        resolved = resolve_button(callback, text)
        if resolved.button is None and resolved.excluded_reason is None:
            missing.append((callback, text))
    return missing
