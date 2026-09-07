from __future__ import annotations

from html import escape
import math
from typing import Any

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

import config
from authorization import is_staff
from text_templates import fields
from message_catalog import UI_TITLES
from handlers.premium_ui_clean_buttons import _canonical_label, _load_clean_buttons
from handlers.premium_ui_admin import MESSAGE_TITLES
from premium_ui_service import MessageTemplateItem, PremiumUIError, premium_ui_service
from style_engine import style_engine


ui_editor_v2_router = Router(name="ui_editor_v2")


class UIMessageEditStates(StatesGroup):
    body = State()


TRIAL_MESSAGE_TITLES = {
    "trial_v2_root": "صفحه انتخاب تست رایگان",
    "trial_v2_config_select": "انتخاب سرویس کانفیگ تست",
    "trial_v2_panel_select": "انتخاب سرویس پنل تست",
    "trial_v2_config_success": "نتیجه کانفیگ تست",
    "trial_v2_panel_username": "درخواست نام کاربری پنل تست",
    "trial_v2_panel_success": "نتیجه پنل تست",
}

MESSAGE_CATEGORY_META: list[tuple[str, str, str]] = [
    ("trial", "تست رایگان", "🧪"),
    ("sales", "فروش و انتخاب پلن", "🛒"),
    ("payment", "پرداخت", "💳"),
    ("orders", "سفارش‌ها و تحویل", "📦"),
    ("support", "پشتیبانی", "🎧"),
    ("panel", "پنل و کاربران", "🧩"),
    ("start", "شروع و دسترسی", "🏠"),
    ("system", "هشدارها و خطاها", "⚠️"),
    ("backup", "بکاپ", "🧰"),
    ("other", "سایر متن‌ها", "📝"),
]

BUTTON_CATEGORY_META: list[tuple[str, str, str]] = [
    ("main", "منوهای اصلی", "🏠"),
    ("trial", "تست رایگان", "🧪"),
    ("sales", "خرید و پلن‌ها", "🛒"),
    ("payment", "سفارش و پرداخت", "💵"),
    ("panel", "پنل و کاربران", "🧩"),
    ("support", "پشتیبانی", "🎧"),
    ("settings", "تنظیمات", "⚙️"),
    ("manage", "مدیریت ربات", "🧑‍💼"),
    ("other", "سایر دکمه‌ها", "🔘"),
]

VARIABLE_HINTS: dict[str, tuple[str, ...]] = {
    "trial_v2_config_success": ("{traffic}", "{minutes}"),
    "trial_v2_panel_success": ("{username}", "{password}", "{hours}"),
}

PREVIEW_VALUES = {
    "traffic": "1 GB",
    "minutes": "60",
    "username": "arman_test",
    "password": "ExamplePassword",
    "hours": "2",
}


def _sudo(user_id: int) -> bool:
    return is_staff(user_id)


async def _deny(callback: CallbackQuery) -> bool:
    if _sudo(callback.from_user.id):
        return False
    await callback.answer("غیرمجاز", show_alert=True)
    return True


async def _btn(
    text: str,
    callback_data: str,
    *,
    icon_key: str | None = None,
    fallback: str | None = None,
):
    return await style_engine.styled_button(
        text,
        callback_data=callback_data,
        icon_key=icon_key,
        fallback=fallback,
    )


def _message_title(key: str) -> str:
    return UI_TITLES.get(key) or TRIAL_MESSAGE_TITLES.get(key) or MESSAGE_TITLES.get(key, "متن عمومی")


def _message_category(key: str) -> str:
    if key.startswith("support_"):
        return "support"
    if key == "customer_home":
        return "start"
    if key.startswith("sales_"):
        return "sales"
    if key.startswith("order_") or key == "public_order_registered":
        return "orders"
    if "payment" in key or "receipt" in key:
        return "payment"
    if key.startswith("trial_v2_"):
        return "trial"
    if key.startswith("backup_"):
        return "backup"
    if key in {"welcome_sudo", "welcome_admin", "unauthorized"}:
        return "start"
    if key.startswith("public_") or key.startswith("order_"):
        return "sales"
    if key.startswith("admin_") or key.startswith("panel_") or "users_" in key:
        return "panel"
    if any(word in key for word in ("error", "invalid", "warning", "exceeded", "not_found")):
        return "system"
    return "other"


def _button_category(callback: str) -> str:
    cb = str(callback or "").lower()
    if cb.startswith("support:") or "ticket" in cb:
        return "support"
    if "settings" in cb:
        return "settings"
    if "trial" in cb or "paneltest" in cb:
        return "trial"
    if cb in {"back_to_main", "back_to_admin_main", "public_back_main", "svcmarket:home"}:
        return "main"
    if cb.startswith("svcmarket:") or "buy_reseller" in cb or cb.startswith("sales_"):
        return "sales"
    if cb.startswith(("order_", "admin_order_", "public_order_")) or any(
        word in cb for word in ("payment", "receipt", "card")
    ):
        return "payment"
    if any(word in cb for word in ("panel", "user", "admin")) and not cb.startswith("cc:botadmins"):
        return "panel"
    if cb.startswith(("cc:", "sudo_menu_", "style:", "rsvc:", "rebecca_", "pui:")):
        return "manage"
    return "other"


async def _message_groups() -> dict[str, list[MessageTemplateItem]]:
    groups = {key: [] for key, _title, _emoji in MESSAGE_CATEGORY_META}
    for item in await premium_ui_service.list_messages():
        groups.setdefault(_message_category(item.key), []).append(item)
    for items in groups.values():
        items.sort(key=lambda item: _message_title(item.key))
    return groups


@ui_editor_v2_router.callback_query(F.data == "cc:texts")
async def message_categories(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    await state.clear()
    groups = await _message_groups()
    rows = []
    for key, title, emoji in MESSAGE_CATEGORY_META:
        count = len(groups.get(key, []))
        if count:
            rows.append([await _btn(f"{emoji} {title} · {count}", f"uiv2:mc:{key}")])
    rows.extend([
        [await _btn("Premium Emojiها", "style:emojis", icon_key="style", fallback="✨")],
        [await _btn("خانه", "back_to_main", icon_key="home", fallback="🏠")],
    ])
    await callback.message.edit_text(
        "📝 <b>مدیریت متن‌های ربات</b>\n\n"
        "متن‌ها بر اساس بخشی که کاربر می‌بیند دسته‌بندی شده‌اند.\n"
        "اول بخش را انتخاب کنید؛ بعد فقط متن‌های همان قسمت را می‌بینید.\n\n"
        "Premium Emoji: <code>{emoji:key}</code>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@ui_editor_v2_router.callback_query(F.data.startswith("uiv2:mc:"))
async def message_category(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    await state.clear()
    parts = (callback.data or "").split(":")
    category = parts[2] if len(parts) >= 3 else ""
    try:
        page = int(parts[3]) if len(parts) == 4 else 0
    except ValueError:
        page = 0
    meta = next((item for item in MESSAGE_CATEGORY_META if item[0] == category), None)
    if not meta:
        await callback.answer("بخش نامعتبر", show_alert=True)
        return
    groups = await _message_groups()
    items = groups.get(category, [])
    rows = []
    pages = max(1, math.ceil(len(items) / 10))
    page = max(0, min(page, pages - 1))
    for item in items[page*10:(page+1)*10]:
        status = "✨" if item.is_overridden else "▫️"
        rows.append([await _btn(f"{status} {_message_title(item.key)}"[:58], f"uiv2:m:{item.key}")])
    nav = []
    if page:
        nav.append(await _btn("قبلی", f"uiv2:mc:{category}:{page-1}"))
    if page+1 < pages:
        nav.append(await _btn("بعدی", f"uiv2:mc:{category}:{page+1}"))
    if nav:
        rows.append(nav)
    rows.append([await _btn("بازگشت به بخش‌ها", "cc:texts", icon_key="back", fallback="⬅️")])
    await callback.message.edit_text(
        f"{meta[2]} <b>{escape(meta[1])}</b>\n\n"
        "متنی را که می‌خواهید تغییر دهید انتخاب کنید.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


async def _render_message_detail(message: Message, key: str) -> None:
    item = await premium_ui_service.get_message(key)
    if not item:
        await message.edit_text("❌ متن پیدا نشد.")
        return
    category = _message_category(key)
    hints = tuple("{" + name + "}" for name in sorted(fields(item.default_body)))
    hint_text = ""
    if hints:
        hint_text = "\nمتغیرهای قابل استفاده: " + "، ".join(f"<code>{escape(value)}</code>" for value in hints)
    text = (
        f"📝 <b>{escape(_message_title(key))}</b>\n\n"
        f"وضعیت: {'✨ سفارشی' if item.is_overridden else '▫️ پیش‌فرض'}"
        f"{hint_text}\n"
        "Premium Emoji: <code>{emoji:key}</code>\n\n"
        "متن فعلی:\n"
        f"<pre>{escape(item.body)}</pre>"
    )
    rows = [
        [await _btn("ویرایش متن", f"uiv2:me:{key}", fallback="✏️")],
        [await _btn("پیش‌نمایش", f"uiv2:mp:{key}", fallback="👁")],
        [await _btn("بازگردانی پیش‌فرض", f"uiv2:mr:{key}", fallback="♻️")],
        [await _btn("بازگشت", f"uiv2:mc:{category}", icon_key="back", fallback="⬅️")],
    ]
    await message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@ui_editor_v2_router.callback_query(F.data.startswith("uiv2:m:"))
async def message_detail(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    await state.clear()
    key = (callback.data or "").split(":", 2)[-1]
    await _render_message_detail(callback.message, key)
    await callback.answer()


@ui_editor_v2_router.callback_query(F.data.startswith("uiv2:me:"))
async def message_edit_start(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    key = (callback.data or "").split(":", 2)[-1]
    item = await premium_ui_service.get_message(key)
    if not item:
        await callback.answer("متن پیدا نشد", show_alert=True)
        return
    await state.clear()
    await state.update_data(uiv2_message_key=key)
    await state.set_state(UIMessageEditStates.body)
    hints = tuple("{" + name + "}" for name in sorted(fields(item.default_body)))
    hint_text = ""
    if hints:
        hint_text = "\nمتغیرها را حذف نکنید: " + "، ".join(hints)
    await callback.message.answer(
        "✏️ متن جدید را ارسال کنید.\n"
        "برای ایموجی پرمیوم بنویسید: <code>{emoji:wire}</code>"
        + hint_text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[await _btn("لغو و بازگشت", f"uiv2:m:{key}")]])
    )
    await callback.answer()


@ui_editor_v2_router.message(UIMessageEditStates.body, F.text)
async def message_edit_value(message: Message, state: FSMContext):
    if not _sudo(message.from_user.id):
        return
    data = await state.get_data()
    key = str(data.get("uiv2_message_key") or "")
    try:
        body = premium_ui_service.validate_message(key, message.text or "")
    except PremiumUIError as exc:
        await message.answer(f"❌ {escape(str(exc))}")
        return
    await state.update_data(uiv2_message_draft=body)
    item = await premium_ui_service.get_message(key)
    await message.answer(await _preview_body(item, body), reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [await _btn("ذخیره تغییرات", "uiv2:save", fallback="✅")],
        [await _btn("لغو و بازگشت", f"uiv2:m:{key}", fallback="⬅️")],
    ]))


async def _preview_body(item, body):
    for name in fields(item.default_body):
        body = body.replace("{" + name + "}", escape(PREVIEW_VALUES.get(name, "نمونه")))
    return await premium_ui_service.render_placeholders(body)


@ui_editor_v2_router.callback_query(F.data == "uiv2:save", UIMessageEditStates.body)
async def message_save_draft(callback, state):
    if await _deny(callback):
        return
    data = await state.get_data()
    key, body = data.get("uiv2_message_key", ""), data.get("uiv2_message_draft", "")
    try:
        await premium_ui_service.set_message(key, body)
    except PremiumUIError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await state.clear()
    await _render_message_detail(callback.message, key)
    await callback.answer("ذخیره شد")


@ui_editor_v2_router.callback_query(F.data.startswith("uiv2:mr:"))
async def message_reset(callback: CallbackQuery):
    if await _deny(callback):
        return
    key = (callback.data or "").split(":", 2)[-1]
    try:
        await premium_ui_service.reset_message(key)
    except PremiumUIError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await _render_message_detail(callback.message, key)
    await callback.answer("متن به حالت پیش‌فرض برگشت")


@ui_editor_v2_router.callback_query(F.data.startswith("uiv2:mp:"))
async def message_preview(callback: CallbackQuery):
    if await _deny(callback):
        return
    key = (callback.data or "").split(":", 2)[-1]
    item = await premium_ui_service.get_message(key)
    if not item:
        await callback.answer("متن پیدا نشد", show_alert=True)
        return
    await callback.message.answer(await _preview_body(item, item.body))
    await callback.answer("پیش‌نمایش ارسال شد")


async def _button_groups() -> dict[str, list[Any]]:
    groups = {key: [] for key, _title, _emoji in BUTTON_CATEGORY_META}
    for item in await _load_clean_buttons():
        groups.setdefault(_button_category(item.callback_data), []).append(item)
    return groups


@ui_editor_v2_router.callback_query(F.data == "cc:buttons")
async def button_categories(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    await state.clear()
    groups = await _button_groups()
    rows = []
    for key, title, emoji in BUTTON_CATEGORY_META:
        count = len(groups.get(key, []))
        if count:
            rows.append([await _btn(f"{emoji} {title} · {count}", f"uiv2:bc:{key}:0")])
    rows.extend([
        [await _btn("Premium Emojiها", "style:emojis", icon_key="style", fallback="✨")],
        [await _btn("خانه", "back_to_main", icon_key="home", fallback="🏠")],
    ])
    await callback.message.edit_text(
        "🔘 <b>متن و ایموجی دکمه‌ها</b>\n\n"
        "دیگر لازم نیست بین ده‌ها دکمه بگردید.\n"
        "اول بخش را انتخاب کنید و بعد دکمه همان قسمت را تغییر دهید.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@ui_editor_v2_router.callback_query(F.data.startswith("uiv2:bc:"))
async def button_category(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    await state.clear()
    parts = (callback.data or "").split(":")
    if len(parts) != 4:
        await callback.answer("بخش نامعتبر", show_alert=True)
        return
    category = parts[2]
    try:
        page = int(parts[3])
    except ValueError:
        page = 0
    meta = next((item for item in BUTTON_CATEGORY_META if item[0] == category), None)
    if not meta:
        await callback.answer("بخش نامعتبر", show_alert=True)
        return
    items = (await _button_groups()).get(category, [])
    page_size = 10
    pages = max(1, math.ceil(len(items) / page_size))
    page = max(0, min(page, pages - 1))
    visible = items[page * page_size : (page + 1) * page_size]
    rows = []
    for item in visible:
        label = item.display_text or _canonical_label(item.default_text)
        suffix = f" · ✨ {item.emoji_key}" if item.emoji_key else ""
        rows.append([await _btn(f"{label}{suffix}"[:58], f"pui:b:{item.id}")])
    if pages > 1:
        nav = []
        if page > 0:
            nav.append(await _btn("قبلی", f"uiv2:bc:{category}:{page-1}", fallback="⬅️"))
        if page + 1 < pages:
            nav.append(await _btn("بعدی", f"uiv2:bc:{category}:{page+1}", fallback="➡️"))
        rows.append(nav)
    rows.append([await _btn("بازگشت به بخش‌ها", "cc:buttons", icon_key="back", fallback="⬅️")])
    await callback.message.edit_text(
        f"{meta[2]} <b>{escape(meta[1])}</b>\n\n"
        "دکمه موردنظر را انتخاب کنید؛ متن و Premium Emoji را می‌توانید جدا تغییر دهید.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@ui_editor_v2_router.callback_query(F.data == "uiv2:save")
async def expired_message_draft(callback):
    await callback.answer("این پیش‌نویس منقضی شده است؛ متن را دوباره باز کنید.", show_alert=True)
