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
    "common.navigation": ScreenDef("common.navigation", "customer", "main", "ناوبری عمومی", 90),
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
    "support.home": ScreenDef("support.home", "customer", "support", "پشتیبانی", 0),

    # Management
    "admin.dashboard": ScreenDef("admin.dashboard", "manage", "dashboard", "مرکز مدیریت ربات", 0),
    "admin.panels": ScreenDef("admin.panels", "manage", "products", "مرکز پنل‌ها", 0),
    "admin.products": ScreenDef("admin.products", "manage", "products", "محصولات و پلن‌ها", 1),
    "admin.rebecca": ScreenDef("admin.rebecca", "manage", "products", "سرویس‌های Rebecca", 2),
    "admin.orders": ScreenDef("admin.orders", "manage", "orders", "سفارش‌ها", 0),
    "admin.users": ScreenDef("admin.users", "manage", "users", "کاربران", 0),
    "admin.finance": ScreenDef("admin.finance", "manage", "finance", "مالی و پرداخت", 0),
    "admin.discounts": ScreenDef("admin.discounts", "manage", "discounts", "مدیریت تخفیف‌ها", 0),
    "admin.trial": ScreenDef("admin.trial", "manage", "trial_admin", "تنظیمات تست رایگان", 0),
    "admin.support": ScreenDef("admin.support", "manage", "support_admin", "پشتیبانی و تیکت", 0),
    "admin.access": ScreenDef("admin.access", "manage", "access", "ادمین‌ها و دسترسی", 0),
    "admin.reports": ScreenDef("admin.reports", "manage", "reports", "آمار و گزارشات", 0),
    "admin.broadcast": ScreenDef("admin.broadcast", "manage", "broadcast", "اطلاع‌رسانی", 0),
    "admin.content": ScreenDef("admin.content", "manage", "content", "متن، دکمه و استایل", 0),
    "admin.settings": ScreenDef("admin.settings", "manage", "settings", "تنظیمات", 0),
    "admin.tools": ScreenDef("admin.tools", "manage", "tools", "ابزارها و بکاپ", 0),
    "admin.navigation": ScreenDef("admin.navigation", "manage", "dashboard", "ناوبری مدیریت", 90),
}


def _clean(value: str | None) -> str:
    text = str(value or "").strip()
    text = re.sub(r"^[^\w@]+", "", text, flags=re.UNICODE).strip()
    return re.sub(r"\s+", " ", text)


BUTTONS: tuple[ButtonDef, ...] = (
    # Public/reseller home
    ButtonDef("public.home.buy", "public.home", "خرید پنل نمایندگی", "public_buy_reseller", rank=0),
    ButtonDef("public.home.trial", "public.home", "تست رایگان", "svcmarket:trial", rank=1),
    ButtonDef("public.home.support", "public.home", "پشتیبانی", "support:home", rank=2),
    ButtonDef("reseller.home.info", "reseller.home", "اطلاعات من", "my_info", rank=0),
    ButtonDef("reseller.home.report", "reseller.home", "گزارش من", "my_report", rank=1),
    ButtonDef("reseller.home.users", "reseller.home", "کاربران من", "my_users", rank=2),
    ButtonDef("reseller.home.reactivate", "reseller.home", "فعال‌سازی کاربران", "reactivate_users", rank=3),
    ButtonDef("reseller.home.buy", "reseller.home", "خرید پنل نمایندگی", "admin_buy_reseller", rank=4),
    ButtonDef("reseller.home.renew", "reseller.home", "تمدید/افزایش", "admin_renew", rank=5),
    ButtonDef("reseller.home.trial", "reseller.home", "تست رایگان", "svcmarket:trial", rank=6),
    ButtonDef("reseller.home.support", "reseller.home", "پشتیبانی", "support:home", rank=7),

    # Trial customer UI
    ButtonDef("trial.home.config", "trial.home", "کانفیگ تست", "trialv2:choose:config", rank=0),
    ButtonDef("trial.home.panel", "trial.home", "پنل نمایندگی تست", "trialv2:choose:panel", rank=1),
    ButtonDef("trial.home.back", "trial.home", "بازگشت", "svcmarket:home", rank=90, navigation_type="back"),
    ButtonDef("trial.config.back", "trial.config", "بازگشت", "trialv2:root", rank=90, navigation_type="back"),
    ButtonDef("trial.panel.back", "trial.panel", "بازگشت", "trialv2:root", rank=90, navigation_type="back"),

    # Support
    ButtonDef("support.home.new", "support.home", "ایجاد تیکت", "support:new", rank=0),
    ButtonDef("support.home.back", "support.home", "بازگشت", "public_back_main", rank=90, navigation_type="back"),

    # Management dashboard; duplicate callback is disambiguated by default_text.
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

    # Discount management stable actions
    ButtonDef("admin.discounts.add", "admin.discounts", "ساخت کد تخفیف", "ops:disc:add", rank=0),
    ButtonDef("admin.discounts.percent", "admin.discounts", "درصدی", "ops:disc:type:percent", rank=10),
    ButtonDef("admin.discounts.fixed", "admin.discounts", "مبلغ ثابت", "ops:disc:type:fixed", rank=11),
    ButtonDef("admin.discounts.back", "admin.discounts", "بازگشت", "back_to_main", rank=90, navigation_type="back"),

    # Trial management stable actions
    ButtonDef("admin.trial.config", "admin.trial", "تنظیم کانفیگ تست", "ux:trialadmin:config", rank=0),
    ButtonDef("admin.trial.panel", "admin.trial", "تنظیم پنل تست", "ux:trialadmin:panel", rank=1),
    ButtonDef("admin.trial.services", "admin.trial", "پنل‌های قابل تست", "ux:trialadmin:plans", rank=2),

    # Shared management navigation
    ButtonDef("admin.navigation.home", "admin.navigation", "خانه", "back_to_main", rank=0, navigation_type="home"),
)


_BY_CALLBACK: dict[str, list[ButtonDef]] = {}
for _button in BUTTONS:
    _BY_CALLBACK.setdefault(_button.callback_data, []).append(_button)


# Internal/legacy callbacks are intentionally hidden from the normal editor.
# They remain functional so old Telegram messages continue to work.
_HIDDEN_PREFIX_REASONS: tuple[tuple[str, str], ...] = (
    ("pui:", "کنترل داخلی ویرایشگر"),
    ("puc:", "کنترل داخلی ویرایشگر"),
    ("uiv2:", "رابط قدیمی ویرایشگر"),
    ("uiv3:", "کنترل داخلی ویرایشگر"),
    ("uiv4:", "کنترل داخلی ویرایشگر"),
    ("svcmarket:trial:config", "مسیر سازگاری قدیمی تست"),
    ("svcmarket:trial:panel", "مسیر سازگاری قدیمی تست"),
    ("ops:configtrial:request", "مسیر دسترسی داخلی تست"),
    ("ops:paneltrial:request", "مسیر دسترسی داخلی تست"),
    ("ops:trial:request", "مسیر دسترسی داخلی تست"),
)

_DYNAMIC_PREFIX_REASONS: tuple[tuple[str, str], ...] = (
    ("planmarket:c:", "نام پنل/دسته پویا"),
    ("planmarket:d:", "دسته زمانی پویا"),
    ("planmarket:p:", "پلن پویا"),
    ("trialv2:cfg:", "پنل تست پویا"),
    ("trialv2:panel:", "پنل تست پویا"),
    ("cc:user:", "کاربر پویا"),
    ("cc:order:", "سفارش پویا"),
    ("cc:receipt:", "رسید سفارش پویا"),
    ("ops:disc:item:", "کد تخفیف پویا"),
    ("ops:disc:toggle:", "عملیات روی کد تخفیف پویا"),
    ("ops:disc:delete:", "عملیات روی کد تخفیف پویا"),
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
    reason = _excluded(callback)
    if reason:
        return ButtonResolution(None, reason)

    candidates = _BY_CALLBACK.get(callback, [])
    if not candidates:
        return ButtonResolution(None, None)
    if len(candidates) == 1:
        return ButtonResolution(candidates[0], None)

    wanted = _clean(default_text)
    for candidate in candidates:
        if candidate.default_text and _clean(candidate.default_text) == wanted:
            return ButtonResolution(candidate, None)
    # Ambiguous callback without matching presentation identity: hide rather than
    # guessing which visible screen the administrator meant to customize.
    return ButtonResolution(None, "callback مشترک با چند دکمه نمایشی")


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
