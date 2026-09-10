from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Any

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup

from authorization import is_staff
from handlers.premium_ui_clean_buttons import _canonical_label, _load_clean_buttons
from style_engine import style_engine


button_editor_context_router = Router(name="button_editor_context")


@dataclass(frozen=True)
class ButtonContext:
    scope: str
    category: str
    context: str
    rank: int = 50


SCOPE_META = {
    "customer": ("کاربر و نماینده", "👤"),
    "manage": ("مدیریت ربات", "🛠"),
}

CATEGORY_META = {
    "main": ("منو و ناوبری", "🏠"),
    "trial": ("تست رایگان", "🧪"),
    "sales": ("خرید و پلن‌ها", "🛒"),
    "account": ("پنل و حساب", "🧩"),
    "payment": ("پرداخت و سفارش", "💳"),
    "support": ("پشتیبانی", "🎧"),
    "dashboard": ("داشبورد مدیریت", "🧭"),
    "products": ("محصولات و پنل‌ها", "📦"),
    "orders": ("سفارش‌ها", "🧾"),
    "users": ("کاربران", "👥"),
    "discounts": ("تخفیف‌ها", "🎟"),
    "trial_admin": ("تنظیمات تست", "🧪"),
    "support_admin": ("تیکت و پشتیبانی", "🎧"),
    "content": ("متن، دکمه و ظاهر", "🎨"),
    "access": ("ادمین‌ها و دسترسی", "🔐"),
    "reports": ("آمار و گزارش", "📊"),
    "broadcast": ("اطلاع‌رسانی", "📢"),
    "settings": ("تنظیمات", "⚙️"),
    "tools": ("ابزارها و بکاپ", "🧰"),
    "other": ("سایر", "🔘"),
}

# Exact stable controls get a human screen path.  This is deliberately about
# presentation context, never business identity/callback mutation.
_EXACT: dict[str, ButtonContext] = {
    # Customer / reseller home.
    "public_buy_reseller": ButtonContext("customer", "sales", "منوی مشتری → خرید پنل", 0),
    "admin_buy_reseller": ButtonContext("customer", "sales", "منوی نماینده → خرید پنل", 1),
    "svcmarket:trial": ButtonContext("customer", "trial", "منوی اصلی → تست رایگان", 0),
    "support:home": ButtonContext("customer", "support", "منوی اصلی → پشتیبانی", 0),
    "support:new": ButtonContext("customer", "support", "پشتیبانی → ایجاد تیکت", 1),
    "my_info": ButtonContext("customer", "account", "منوی نماینده → اطلاعات من", 0),
    "my_report": ButtonContext("customer", "account", "منوی نماینده → گزارش من", 1),
    "my_users": ButtonContext("customer", "account", "منوی نماینده → کاربران من", 2),
    "reactivate_users": ButtonContext("customer", "account", "منوی نماینده → فعال‌سازی کاربران", 3),
    "admin_renew": ButtonContext("customer", "account", "منوی نماینده → تمدید/افزایش", 4),
    "back_to_admin_main": ButtonContext("customer", "main", "ناوبری → منوی نماینده", 0),
    "public_back_main": ButtonContext("customer", "main", "ناوبری → منوی مشتری", 1),
    "svcmarket:home": ButtonContext("customer", "main", "ناوبری → صفحه اصلی", 2),
    "trialv2:choose:config": ButtonContext("customer", "trial", "تست رایگان → کانفیگ تست", 1),
    "trialv2:choose:panel": ButtonContext("customer", "trial", "تست رایگان → پنل تست", 2),
    "trialv2:root": ButtonContext("customer", "trial", "ناوبری تست → صفحه انتخاب تست", 3),
    "svcmarket:trial:config": ButtonContext("customer", "trial", "مسیر سازگاری → کانفیگ تست", 20),
    "svcmarket:trial:panel": ButtonContext("customer", "trial", "مسیر سازگاری → پنل تست", 21),
    "ops:configtrial:request": ButtonContext("customer", "trial", "دسترسی کاربر → کانفیگ تست", 22),
    "ops:paneltrial:request": ButtonContext("customer", "trial", "دسترسی کاربر → پنل تست", 23),
    "ops:trial:request": ButtonContext("customer", "trial", "دسترسی کاربر → تست رایگان", 24),

    # Main operational dashboard.
    "sudo_menu_sales": ButtonContext("manage", "dashboard", "مرکز مدیریت → فروش/مالی", 0),
    "sudo_menu_panels": ButtonContext("manage", "dashboard", "مرکز مدیریت → مرکز پنل‌ها", 1),
    "cc:orders:0": ButtonContext("manage", "dashboard", "مرکز مدیریت → سفارش‌ها", 2),
    "sales_manage": ButtonContext("manage", "dashboard", "مرکز مدیریت → محصولات و پلن‌ها", 3),
    "cc:users:0": ButtonContext("manage", "dashboard", "مرکز مدیریت → کاربران", 4),
    "cc:discounts": ButtonContext("manage", "dashboard", "مرکز مدیریت → تخفیف‌ها", 5),
    "cc:test": ButtonContext("manage", "dashboard", "مرکز مدیریت → تست رایگان", 6),
    "cc:botadmins": ButtonContext("manage", "dashboard", "مرکز مدیریت → ادمین‌های ربات", 7),
    "cc:stats": ButtonContext("manage", "dashboard", "مرکز مدیریت → آمار و گزارشات", 8),
    "sudo_menu_broadcast": ButtonContext("manage", "dashboard", "مرکز مدیریت → اطلاع‌رسانی", 9),
    "cc:tickets:0": ButtonContext("manage", "dashboard", "مرکز مدیریت → تیکت‌ها", 10),
    "cc:texts": ButtonContext("manage", "content", "مرکز مدیریت → مدیریت متن‌ها", 0),
    "cc:buttons": ButtonContext("manage", "content", "مرکز مدیریت → مدیریت دکمه‌ها", 1),
    "sudo_menu_backup": ButtonContext("manage", "tools", "مرکز مدیریت → ابزارها و بکاپ", 0),
    "sudo_menu_settings": ButtonContext("manage", "settings", "مرکز مدیریت → تنظیمات", 0),
    "back_to_main": ButtonContext("manage", "main", "ناوبری مدیریت → خانه", 0),

    # Trial administration is intentionally separated from customer trial UI.
    "ux:trialadmin:config": ButtonContext("manage", "trial_admin", "تنظیمات تست → کانفیگ", 0),
    "ux:trialadmin:panel": ButtonContext("manage", "trial_admin", "تنظیمات تست → پنل نمایندگی", 1),
    "ux:trialadmin:plans": ButtonContext("manage", "trial_admin", "تنظیمات تست → پنل‌های مجاز", 2),
}


_INTERNAL_PREFIXES = ("pui:", "puc:", "uiv2:", "uiv3:")

# These prefixes normally point at a record whose visible label is data (plan,
# panel, user, order, ticket, discount...).  They belong in their domain manager,
# not in the global text/emoji editor.  Stable action buttons under the same
# feature are handled before this check by the exact/prefix rules below.
_DATA_PREFIXES = (
    "planmarket:c:",
    "planmarket:d:",
    "planmarket:p:",
    "trialv2:cfg:",
    "trialv2:panel:",
    "cc:user:",
    "cc:order:",
    "cc:receipt:",
    "ops:disc:item:",
)


def _has_record_id(callback: str) -> bool:
    return bool(re.search(r"(?:^|[:_])\d+(?:[:_]|$)", callback))


def _looks_like_navigation(text: str) -> bool:
    return _canonical_label(text) in {
        "بازگشت", "خانه", "قبلی", "بعدی", "لغو", "لغو و بازگشت",
        "بازگشت به بخش‌ها", "بازگشت به سفارش‌ها", "بازگشت به دکمه",
    }


def button_context(item: Any) -> ButtonContext | None:
    """Classify every editable button by audience + screen context.

    The old editor classified only from substring matches in callback_data, so
    customer controls, admin settings, records and duplicate Back buttons could
    land in the same bucket.  This resolver is intentionally global and is used
    for every category, not just the trial screen.
    """
    callback = str(getattr(item, "callback_data", "") or "")
    text = str(getattr(item, "default_text", "") or "")
    cb = callback.lower()

    if not callback or callback.startswith(_INTERNAL_PREFIXES):
        return None
    if callback in _EXACT:
        return _EXACT[callback]

    # Dynamic data rows are edited in their real manager (products/users/orders),
    # not as arbitrary button text.  This also eliminates ID/name noise globally.
    if callback.startswith(_DATA_PREFIXES):
        return None

    # Customer/reseller feature families.
    if cb.startswith("support:"):
        return ButtonContext("customer", "support", "پشتیبانی → عملیات", 10)
    if cb.startswith("trialv2:") or cb.startswith("svcmarket:trial"):
        return ButtonContext("customer", "trial", "تست رایگان → عملیات", 10)
    if cb.startswith("planmarket:root:"):
        return ButtonContext("customer", "sales", "خرید → بازگشت به لیست پنل‌ها", 20)
    if cb.startswith(("public_mark_paid_", "public_order_", "admin_order_")):
        # Concrete order IDs are business data; only expose a stable action label.
        if _looks_like_navigation(text):
            return ButtonContext("customer", "main", "ناوبری خرید", 30)
        if _has_record_id(callback):
            current = _canonical_label(text)
            if current in {"پرداخت کردم", "ثبت پرداخت", "انتخاب این پلن", "خرید این پلن"}:
                return ButtonContext("customer", "payment" if "پرداخت" in current else "sales", "خرید → اقدام اصلی", 30)
            return None
    if cb.startswith(("admin_full_renew", "admin_increase", "admin_renew_")):
        if _has_record_id(callback) and not _looks_like_navigation(text):
            return None
        return ButtonContext("customer", "account", "پنل نماینده → تمدید و افزایش", 20)

    # Management feature families.  Record rows are filtered, while stable
    # controls remain editable in the correct administration section.
    if cb.startswith("ops:disc:"):
        if _has_record_id(callback):
            return None
        return ButtonContext("manage", "discounts", "مدیریت تخفیف‌ها → عملیات", 10)
    if cb.startswith(("ops:trial:", "ux:trialadmin:", "ux:paneltrial:")):
        return ButtonContext("manage", "trial_admin", "تنظیمات تست → عملیات", 10)
    if cb.startswith(("pc:", "product:", "rsvc:", "rebecca_", "sudo_panel", "sudo_menu_panels")):
        if _has_record_id(callback) and not _looks_like_navigation(text):
            return None
        return ButtonContext("manage", "products", "محصولات و پنل‌ها → عملیات", 10)
    if cb.startswith(("order_approve_", "order_reject_", "order_retry_")):
        # Same operational action can appear for many order IDs; listing every
        # concrete order would be misleading, so keep them out of the editor.
        return None
    if cb.startswith("cc:orders:"):
        if callback != "cc:orders:0":
            return None
        return ButtonContext("manage", "orders", "سفارش‌ها → لیست", 10)
    if cb.startswith("cc:users:"):
        if callback != "cc:users:0":
            return None
        return ButtonContext("manage", "users", "کاربران → لیست", 10)
    if cb.startswith("cc:tickets:") or cb.startswith("ticket:"):
        if _has_record_id(callback):
            return None
        return ButtonContext("manage", "support_admin", "تیکت‌ها → عملیات", 10)
    if cb.startswith("cc:botadmin") or cb.startswith("ops:botadmin"):
        return ButtonContext("manage", "access", "ادمین‌های ربات → عملیات", 10)
    if cb.startswith(("sudo_menu_reports", "cc:stats")):
        return ButtonContext("manage", "reports", "آمار و گزارش → عملیات", 10)
    if cb.startswith(("sudo_menu_broadcast", "broadcast")):
        return ButtonContext("manage", "broadcast", "اطلاع‌رسانی → عملیات", 10)
    if cb.startswith(("sudo_menu_backup", "backup")):
        return ButtonContext("manage", "tools", "بکاپ و ابزارها → عملیات", 10)
    if "settings" in cb or cb.startswith("setting"):
        return ButtonContext("manage", "settings", "تنظیمات → عملیات", 10)
    if cb.startswith(("cc:", "sudo_menu_", "sales_")):
        # Remaining stable admin controls are still separated from customer UI.
        if _has_record_id(callback):
            return None
        return ButtonContext("manage", "dashboard", "مدیریت ربات → عملیات", 40)

    # A static unknown button is safer in a separate bucket than being silently
    # mixed into an unrelated feature. Dynamic records stay hidden.
    if _has_record_id(callback):
        return None
    return ButtonContext("manage", "other", "سایر دکمه‌های ثابت", 90)


def _current_label(item: Any) -> str:
    return str(getattr(item, "display_text", None) or _canonical_label(getattr(item, "default_text", "")))


async def _btn(text: str, callback_data: str, *, fallback: str | None = None):
    return await style_engine.styled_button(text, callback_data=callback_data, fallback=fallback)


async def _classified_items() -> list[tuple[ButtonContext, Any]]:
    result: list[tuple[ButtonContext, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for item in await _load_clean_buttons():
        meta = button_context(item)
        if meta is None:
            continue
        # Protect against legacy/new duplicate registrations that differ only by
        # a fallback emoji or identical visible text.
        key = (meta.scope, meta.category, meta.context, _canonical_label(item.default_text))
        if key in seen:
            continue
        seen.add(key)
        result.append((meta, item))
    return result


async def _render_root(message) -> None:
    classified = await _classified_items()
    rows = []
    for scope, (title, emoji) in SCOPE_META.items():
        count = sum(1 for meta, _item in classified if meta.scope == scope)
        if count:
            rows.append([await _btn(f"{emoji} {title} · {count}", f"uiv3:scope:{scope}")])
    rows.extend([
        [await _btn("Premium Emojiها", "style:emojis", fallback="✨")],
        [await _btn("خانه", "back_to_main", fallback="🏠")],
    ])
    await message.edit_text(
        "🔘 <b>مدیریت دکمه‌های ربات</b>\n\n"
        "دکمه‌ها بر اساس <b>سمتی که نمایش داده می‌شوند</b> و <b>صفحه واقعی</b> تفکیک شده‌اند.\n"
        "نام پنل، پلن، کاربر، سفارش و سایر داده‌های پویا اینجا نمایش داده نمی‌شوند؛ "
        "آن‌ها از بخش مدیریتی خودشان ویرایش می‌شوند.\n\n"
        "متن و Premium Emoji قابل تغییر است؛ Callback و منطق دکمه هیچ‌وقت تغییر نمی‌کند.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@button_editor_context_router.callback_query(F.data == "cc:buttons")
async def global_button_editor(callback: CallbackQuery, state: FSMContext):
    if not is_staff(callback.from_user.id):
        await callback.answer("غیرمجاز", show_alert=True)
        return
    await state.clear()
    await _render_root(callback.message)
    await callback.answer()


@button_editor_context_router.callback_query(F.data.startswith("uiv3:scope:"))
async def scope_menu(callback: CallbackQuery, state: FSMContext):
    if not is_staff(callback.from_user.id):
        await callback.answer("غیرمجاز", show_alert=True)
        return
    await state.clear()
    scope = (callback.data or "").rsplit(":", 1)[-1]
    if scope not in SCOPE_META:
        await callback.answer("بخش نامعتبر", show_alert=True)
        return
    classified = await _classified_items()
    counts: dict[str, int] = {}
    for meta, _item in classified:
        if meta.scope == scope:
            counts[meta.category] = counts.get(meta.category, 0) + 1
    rows = []
    for category, count in sorted(counts.items(), key=lambda pair: list(CATEGORY_META).index(pair[0]) if pair[0] in CATEGORY_META else 999):
        title, emoji = CATEGORY_META.get(category, (category, "🔘"))
        rows.append([await _btn(f"{emoji} {title} · {count}", f"uiv3:cat:{scope}:{category}:0")])
    rows.append([await _btn("بازگشت", "cc:buttons", fallback="⬅️")])
    title, emoji = SCOPE_META[scope]
    await callback.message.edit_text(
        f"{emoji} <b>{title}</b>\n\nبخشی را انتخاب کنید. هر دکمه در مرحله بعد با محل دقیق نمایش آن مشخص می‌شود.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@button_editor_context_router.callback_query(F.data.startswith("uiv3:cat:"))
async def category_menu(callback: CallbackQuery, state: FSMContext):
    if not is_staff(callback.from_user.id):
        await callback.answer("غیرمجاز", show_alert=True)
        return
    await state.clear()
    parts = (callback.data or "").split(":")
    if len(parts) != 5:
        await callback.answer("بخش نامعتبر", show_alert=True)
        return
    scope, category = parts[2], parts[3]
    try:
        page = max(0, int(parts[4]))
    except ValueError:
        page = 0
    if scope not in SCOPE_META or category not in CATEGORY_META:
        await callback.answer("بخش نامعتبر", show_alert=True)
        return

    items = [(meta, item) for meta, item in await _classified_items() if meta.scope == scope and meta.category == category]
    items.sort(key=lambda pair: (pair[0].rank, pair[0].context, _current_label(pair[1]), pair[1].id))
    page_size = 9
    pages = max(1, math.ceil(len(items) / page_size))
    page = min(page, pages - 1)
    visible = items[page * page_size:(page + 1) * page_size]

    rows = []
    for meta, item in visible:
        current = _current_label(item)
        suffix = f" · ✨ {item.emoji_key}" if item.emoji_key else ""
        label = f"{meta.context} | {current}{suffix}"
        rows.append([await _btn(label[:60], f"pui:b:{item.id}", fallback="🔘")])

    nav = []
    if page:
        nav.append(await _btn("قبلی", f"uiv3:cat:{scope}:{category}:{page-1}", fallback="⬅️"))
    if page + 1 < pages:
        nav.append(await _btn("بعدی", f"uiv3:cat:{scope}:{category}:{page+1}", fallback="➡️"))
    if nav:
        rows.append(nav)
    rows.append([await _btn("بازگشت به بخش‌ها", f"uiv3:scope:{scope}", fallback="⬅️")])

    title, emoji = CATEGORY_META[category]
    await callback.message.edit_text(
        f"{emoji} <b>{title}</b>\n\n"
        "دکمه موردنظر را از روی <b>محل نمایش → متن فعلی</b> انتخاب کنید.\n"
        "ردیف‌های دارای شناسه/نام پویا عمداً حذف شده‌اند تا اشتباهی متن داده‌های واقعی را تغییر ندهید.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


# Old already-sent category buttons must not reopen the obsolete substring-based
# editor.  Redirect every legacy category, not only the trial category, to the
# new global hierarchy.
@button_editor_context_router.callback_query(F.data.startswith("uiv2:bc:"))
async def legacy_category_redirect(callback: CallbackQuery, state: FSMContext):
    if not is_staff(callback.from_user.id):
        await callback.answer("غیرمجاز", show_alert=True)
        return
    await state.clear()
    await _render_root(callback.message)
    await callback.answer("ساختار مدیریت دکمه‌ها به‌روزرسانی شد")
