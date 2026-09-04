from __future__ import annotations

from html import escape

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

import config
from style_engine import (
    StyleValidationError,
    extract_single_custom_emoji_id,
    style_engine,
)


style_admin_router = Router(name="style_admin")

DEFAULT_STYLE_KEYS: dict[str, str] = {
    "home": "🏠",
    "back": "⬅️",
    "confirm": "✅",
    "cancel": "❌",
    "panel": "🧩",
    "plan": "📦",
    "sales": "🛒",
    "orders": "🧾",
    "users": "👥",
    "finance": "💵",
    "discount": "🎟",
    "test": "🧪",
    "admin": "🧑‍💼",
    "stats": "📊",
    "broadcast": "📢",
    "support": "🎧",
    "style": "🎨",
    "text": "📝",
    "buttons": "🔘",
    "tools": "🧰",
    "settings": "⚙️",
    "rebecca": "🔌",
    "warning": "⚠️",
    "success": "✅",
    "error": "❌",
}


class StyleAdminStates(StatesGroup):
    waiting_for_key = State()
    waiting_for_fallback = State()
    waiting_for_emoji = State()
    waiting_for_replace_confirmation = State()
    waiting_for_fallback_edit = State()
    waiting_for_alias_scope = State()
    waiting_for_alias_raw = State()
    waiting_for_alias_display = State()


def _authorized(user_id: int) -> bool:
    return user_id in config.SUDO_ADMINS


async def _deny(callback: CallbackQuery) -> bool:
    if _authorized(callback.from_user.id):
        return False
    await callback.answer("غیرمجاز", show_alert=True)
    return True


def _parse_numeric_callback(callback: CallbackQuery) -> int | None:
    try:
        return int((callback.data or "").rsplit(":", 1)[-1])
    except (TypeError, ValueError):
        return None


async def _menu_keyboard() -> InlineKeyboardMarkup:
    enabled = await style_engine.is_enabled()
    toggle_label = "خاموش کردن استایل" if enabled else "فعال کردن استایل"
    rows = [
        [await style_engine.styled_button(toggle_label, icon_key="style", fallback="🎨", callback_data="style:toggle")],
        [
            await style_engine.styled_button("Premium Emojis", icon_key="style", fallback="✨", callback_data="style:emojis"),
            await style_engine.styled_button("Text Overrides", icon_key="text", fallback="📝", callback_data="style:aliases"),
        ],
        [await style_engine.styled_button("پیش‌نمایش", icon_key="preview", fallback="👁", callback_data="style:preview")],
        [await style_engine.styled_button("بازگشت", icon_key="back", fallback="⬅️", callback_data="back_to_main")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _render_menu(message: Message) -> None:
    enabled = await style_engine.is_enabled()
    status = "فعال ✅" if enabled else "غیرفعال ⛔"
    icon = await style_engine.render_emoji("style", fallback="🎨")
    await message.edit_text(
        f"{icon} <b>ایموجی و استایل</b>\n\n"
        f"وضعیت سراسری: <b>{status}</b>\n\n"
        "استایل فقط لایه نمایش را تغییر می‌دهد؛ شناسه‌های پنل، پلن، سرویس و callback_data دست‌نخورده می‌مانند.",
        reply_markup=await _menu_keyboard(),
    )


async def _send_menu(message: Message) -> None:
    enabled = await style_engine.is_enabled()
    status = "فعال ✅" if enabled else "غیرفعال ⛔"
    icon = await style_engine.render_emoji("style", fallback="🎨")
    await message.answer(
        f"{icon} <b>ایموجی و استایل</b>\n\n"
        f"وضعیت سراسری: <b>{status}</b>\n\n"
        "استایل فقط لایه نمایش را تغییر می‌دهد؛ شناسه‌های پنل، پلن، سرویس و callback_data دست‌نخورده می‌مانند.",
        reply_markup=await _menu_keyboard(),
    )


async def _render_emoji_list(message: Message) -> None:
    items = await style_engine.list_emojis()
    rows = []
    for item in items:
        state = "✅" if item.enabled else "⛔"
        rows.append([
            await style_engine.styled_button(
                f"{state} {item.key} · {item.fallback_unicode}",
                callback_data=f"style:item:{item.id}",
            )
        ])
    rows.append([await style_engine.styled_button("افزودن / جایگزینی", fallback="➕", callback_data="style:add")])
    rows.append([await style_engine.styled_button("بازگشت", icon_key="back", fallback="⬅️", callback_data="style:menu")])
    text = "✨ <b>Premium Emojis</b>\n\n"
    text += "هنوز ایموجی‌ای ثبت نشده." if not items else f"تعداد ثبت‌شده: <b>{len(items)}</b>"
    await message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


async def _render_emoji_detail(message: Message, item_id: int) -> bool:
    item = await style_engine.get_emoji_by_id(item_id)
    if not item:
        return False
    state = "فعال ✅" if item.enabled else "غیرفعال ⛔"
    toggle = "غیرفعال کردن" if item.enabled else "فعال کردن"
    preview = await style_engine.render_emoji(item.key, fallback=item.fallback_unicode)
    rows = [
        [await style_engine.styled_button("جایگزینی Premium Emoji", fallback="✨", callback_data=f"style:replace:{item.id}")],
        [await style_engine.styled_button("ویرایش Unicode Fallback", fallback="✏️", callback_data=f"style:fallback:{item.id}")],
        [await style_engine.styled_button(toggle, fallback="🔁", callback_data=f"style:itemtoggle:{item.id}")],
        [await style_engine.styled_button("حذف", fallback="🗑", callback_data=f"style:delete:{item.id}")],
        [await style_engine.styled_button("بازگشت", icon_key="back", fallback="⬅️", callback_data="style:emojis")],
    ]
    await message.edit_text(
        "✨ <b>جزئیات ایموجی</b>\n\n"
        f"کلید: <code>{escape(item.key)}</code>\n"
        f"Fallback: {escape(item.fallback_unicode)}\n"
        f"Custom Emoji ID: <code>{escape(item.custom_emoji_id)}</code>\n"
        f"وضعیت: {state}\n"
        f"Preview: {preview}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    return True


async def _render_aliases(message: Message) -> None:
    items = await style_engine.list_overrides()
    rows = []
    for item in items:
        label = f"{item.scope}: {item.raw_identity} → {item.display_text}"
        if len(label) > 50:
            label = label[:47] + "..."
        rows.append([await style_engine.styled_button(label, callback_data=f"style:alias:{item.id}")])
    rows.append([await style_engine.styled_button("افزودن Text Override", fallback="➕", callback_data="style:aliasadd")])
    rows.append([await style_engine.styled_button("بازگشت", icon_key="back", fallback="⬅️", callback_data="style:menu")])
    text = "📝 <b>Text Overrides</b>\n\n"
    text += (
        "هیچ override ثبت نشده."
        if not items
        else "فقط تطبیق دقیق scope + raw identity استفاده می‌شود؛ fuzzy matching وجود ندارد."
    )
    await message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@style_admin_router.message(Command("style"))
async def style_command(message: Message, state: FSMContext):
    if not _authorized(message.from_user.id):
        await message.answer("غیرمجاز")
        return
    await state.clear()
    await _send_menu(message)


@style_admin_router.callback_query(F.data == "sudo_menu_settings")
async def styled_sudo_settings(callback: CallbackQuery, state: FSMContext):
    """Extend the existing SUDO settings screen without changing its callback identity."""
    if await _deny(callback):
        return
    await state.clear()
    settings_icon = await style_engine.render_emoji("settings", fallback="⚙️")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [await style_engine.styled_button(
            "کانال‌های اجباری",
            fallback="📢",
            callback_data="forced_join_manage",
        )],
        [await style_engine.styled_button(
            "ایموجی و استایل",
            icon_key="style",
            fallback="🎨",
            callback_data="style:menu",
        )],
        [await style_engine.styled_button(
            "بازگشت",
            icon_key="back",
            fallback="⬅️",
            callback_data="back_to_main",
        )],
    ])
    await callback.message.edit_text(f"{settings_icon} <b>تنظیمات</b>:", reply_markup=kb)
    await callback.answer()


@style_admin_router.callback_query(F.data == "style:menu")
async def style_menu(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    await state.clear()
    await _render_menu(callback.message)
    await callback.answer()


@style_admin_router.callback_query(F.data == "style:toggle")
async def style_toggle(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    await state.clear()
    await style_engine.set_enabled(not await style_engine.is_enabled())
    await _render_menu(callback.message)
    await callback.answer("ذخیره شد")


@style_admin_router.callback_query(F.data == "style:emojis")
async def style_emojis(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    await state.clear()
    await _render_emoji_list(callback.message)
    await callback.answer()


@style_admin_router.callback_query(F.data == "style:add")
async def style_add(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    await state.clear()
    await state.set_state(StyleAdminStates.waiting_for_key)
    examples = ", ".join(list(DEFAULT_STYLE_KEYS)[:12])
    await callback.message.edit_text(
        "➕ <b>ثبت Premium Emoji</b>\n\n"
        "کلید منطقی را ارسال کنید. این کلید فقط برای لایه نمایش است.\n"
        f"نمونه‌ها: <code>{escape(examples)}</code>\n\n"
        "کلید سفارشی هم مجاز است: a-z / 0-9 / . _ -",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            await style_engine.styled_button("لغو", icon_key="cancel", fallback="❌", callback_data="style:cancel")
        ]]),
    )
    await callback.answer()


@style_admin_router.message(StyleAdminStates.waiting_for_key, F.text)
async def style_key_input(message: Message, state: FSMContext):
    if not _authorized(message.from_user.id):
        return
    try:
        key = style_engine.validate_key(message.text)
    except StyleValidationError as exc:
        await message.answer(f"❌ {escape(str(exc))}")
        return
    existing = await style_engine.get_emoji(key)
    await state.update_data(style_key=key, style_existing_id=(existing.id if existing else None))
    await state.set_state(StyleAdminStates.waiting_for_fallback)
    default = DEFAULT_STYLE_KEYS.get(key)
    hint = f"\nپیشنهاد برای این کلید: {escape(default)}" if default else ""
    await message.answer(
        "Unicode fallback را ارسال کنید. اگر Premium Emoji قابل نمایش نباشد از این مقدار استفاده می‌شود."
        + hint
    )


@style_admin_router.message(StyleAdminStates.waiting_for_fallback, F.text)
async def style_fallback_input(message: Message, state: FSMContext):
    if not _authorized(message.from_user.id):
        return
    try:
        fallback = style_engine.validate_fallback(message.text)
    except StyleValidationError as exc:
        await message.answer(f"❌ {escape(str(exc))}")
        return
    await state.update_data(style_fallback=fallback, style_explicit_replace=False)
    await state.set_state(StyleAdminStates.waiting_for_emoji)
    await message.answer(
        "حالا یک پیام بفرستید که <b>دقیقاً یک Premium Custom Emoji</b> داخل متن یا کپشن آن باشد.\n"
        "ID را دستی وارد نکنید."
    )


@style_admin_router.message(StyleAdminStates.waiting_for_emoji)
async def style_emoji_input(message: Message, state: FSMContext):
    if not _authorized(message.from_user.id):
        return
    try:
        custom_id = extract_single_custom_emoji_id(message)
    except StyleValidationError as exc:
        await message.answer(f"❌ {escape(str(exc))}")
        return
    data = await state.get_data()
    key = data.get("style_key")
    fallback = data.get("style_fallback")
    if not key or not fallback:
        await state.clear()
        await message.answer("❌ وضعیت ثبت منقضی شده. دوباره /style را باز کنید.")
        return

    existing = await style_engine.get_emoji(key)
    if existing and not data.get("style_explicit_replace"):
        await state.update_data(style_candidate_emoji_id=custom_id)
        await state.set_state(StyleAdminStates.waiting_for_replace_confirmation)
        await message.answer(
            f"⚠️ کلید <code>{escape(key)}</code> قبلاً ثبت شده. جایگزین شود؟",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                await style_engine.styled_button("بله، جایگزین کن", icon_key="confirm", fallback="✅", callback_data="style:replaceok"),
                await style_engine.styled_button("لغو", icon_key="cancel", fallback="❌", callback_data="style:cancel"),
            ]]),
        )
        return

    await style_engine.upsert_emoji(key, custom_id, fallback)
    await state.clear()
    await message.answer("✅ Premium Emoji ذخیره شد. با /style می‌توانید نتیجه را ببینید.")


@style_admin_router.callback_query(F.data == "style:replaceok", StyleAdminStates.waiting_for_replace_confirmation)
async def style_replace_confirm(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    data = await state.get_data()
    key = data.get("style_key")
    fallback = data.get("style_fallback")
    custom_id = data.get("style_candidate_emoji_id")
    if not key or not fallback or not custom_id:
        await state.clear()
        await callback.answer("وضعیت منقضی شده", show_alert=True)
        return
    await style_engine.upsert_emoji(key, custom_id, fallback)
    await state.clear()
    await _render_menu(callback.message)
    await callback.answer("جایگزین شد")


@style_admin_router.callback_query(F.data.startswith("style:item:"))
async def style_item(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    await state.clear()
    item_id = _parse_numeric_callback(callback)
    if item_id is None or not await _render_emoji_detail(callback.message, item_id):
        await callback.answer("آیتم پیدا نشد", show_alert=True)
        return
    await callback.answer()


@style_admin_router.callback_query(F.data.startswith("style:replace:"))
async def style_replace_item(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    item_id = _parse_numeric_callback(callback)
    item = await style_engine.get_emoji_by_id(item_id) if item_id is not None else None
    if not item:
        await callback.answer("آیتم پیدا نشد", show_alert=True)
        return
    await state.clear()
    await state.update_data(
        style_key=item.key,
        style_fallback=item.fallback_unicode,
        style_existing_id=item.id,
        style_explicit_replace=True,
    )
    await state.set_state(StyleAdminStates.waiting_for_emoji)
    await callback.message.edit_text(
        f"✨ برای جایگزینی <code>{escape(item.key)}</code> یک پیام شامل دقیقاً یک Premium Custom Emoji بفرستید."
    )
    await callback.answer()


@style_admin_router.callback_query(F.data.startswith("style:fallback:"))
async def style_fallback_edit(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    item_id = _parse_numeric_callback(callback)
    item = await style_engine.get_emoji_by_id(item_id) if item_id is not None else None
    if not item:
        await callback.answer("آیتم پیدا نشد", show_alert=True)
        return
    await state.clear()
    await state.update_data(style_item_id=item.id)
    await state.set_state(StyleAdminStates.waiting_for_fallback_edit)
    await callback.message.edit_text(
        f"Fallback جدید برای <code>{escape(item.key)}</code> را ارسال کنید.\n"
        f"مقدار فعلی: {escape(item.fallback_unicode)}"
    )
    await callback.answer()


@style_admin_router.message(StyleAdminStates.waiting_for_fallback_edit, F.text)
async def style_fallback_edit_input(message: Message, state: FSMContext):
    if not _authorized(message.from_user.id):
        return
    data = await state.get_data()
    item_id = data.get("style_item_id")
    try:
        fallback = style_engine.validate_fallback(message.text)
    except StyleValidationError as exc:
        await message.answer(f"❌ {escape(str(exc))}")
        return
    if not item_id or not await style_engine.update_fallback(int(item_id), fallback):
        await state.clear()
        await message.answer("❌ آیتم پیدا نشد.")
        return
    await state.clear()
    await message.answer("✅ Fallback ذخیره شد. /style")


@style_admin_router.callback_query(F.data.startswith("style:itemtoggle:"))
async def style_item_toggle(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    item_id = _parse_numeric_callback(callback)
    item = await style_engine.get_emoji_by_id(item_id) if item_id is not None else None
    if not item:
        await callback.answer("آیتم پیدا نشد", show_alert=True)
        return
    await style_engine.set_emoji_enabled(item.id, not item.enabled)
    await state.clear()
    await _render_emoji_detail(callback.message, item.id)
    await callback.answer("ذخیره شد")


@style_admin_router.callback_query(F.data.startswith("style:delete:"))
async def style_delete(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    item_id = _parse_numeric_callback(callback)
    item = await style_engine.get_emoji_by_id(item_id) if item_id is not None else None
    if not item:
        await callback.answer("آیتم پیدا نشد", show_alert=True)
        return
    await state.clear()
    await callback.message.edit_text(
        f"🗑 حذف <code>{escape(item.key)}</code> از کاتالوگ استایل؟",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            await style_engine.styled_button("حذف", icon_key="confirm", fallback="✅", callback_data=f"style:deleteok:{item.id}"),
            await style_engine.styled_button("لغو", icon_key="cancel", fallback="❌", callback_data=f"style:item:{item.id}"),
        ]]),
    )
    await callback.answer()


@style_admin_router.callback_query(F.data.startswith("style:deleteok:"))
async def style_delete_confirm(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    item_id = _parse_numeric_callback(callback)
    if item_id is None or not await style_engine.remove_emoji(item_id):
        await callback.answer("آیتم پیدا نشد", show_alert=True)
        return
    await state.clear()
    await _render_emoji_list(callback.message)
    await callback.answer("حذف شد")


@style_admin_router.callback_query(F.data == "style:preview")
async def style_preview(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    await state.clear()
    keys = ["home", "panel", "plan", "sales", "users", "settings", "rebecca", "success", "warning", "error"]
    lines = ["👁 <b>پیش‌نمایش استایل</b>", ""]
    for key in keys:
        fallback = DEFAULT_STYLE_KEYS.get(key, "•")
        lines.append(f"{await style_engine.render_emoji(key, fallback)} <code>{escape(key)}</code>")
    rows = [[await style_engine.styled_button("بازگشت", icon_key="back", fallback="⬅️", callback_data="style:menu")]]
    await callback.message.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


@style_admin_router.callback_query(F.data == "style:aliases")
async def style_aliases(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    await state.clear()
    await _render_aliases(callback.message)
    await callback.answer()


@style_admin_router.callback_query(F.data == "style:aliasadd")
async def style_alias_add(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    await state.clear()
    await state.set_state(StyleAdminStates.waiting_for_alias_scope)
    await callback.message.edit_text(
        "📝 scope را ارسال کنید. نمونه: <code>panel</code>، <code>plan</code>، <code>category</code>، <code>menu</code>"
    )
    await callback.answer()


@style_admin_router.message(StyleAdminStates.waiting_for_alias_scope, F.text)
async def style_alias_scope_input(message: Message, state: FSMContext):
    if not _authorized(message.from_user.id):
        return
    try:
        scope = style_engine.validate_scope(message.text)
    except StyleValidationError as exc:
        await message.answer(f"❌ {escape(str(exc))}")
        return
    await state.update_data(style_alias_scope=scope)
    await state.set_state(StyleAdminStates.waiting_for_alias_raw)
    await message.answer("شناسه/نام خام را دقیقاً همان‌طور که در منطق فعلی ذخیره شده ارسال کنید.")


@style_admin_router.message(StyleAdminStates.waiting_for_alias_raw, F.text)
async def style_alias_raw_input(message: Message, state: FSMContext):
    if not _authorized(message.from_user.id):
        return
    try:
        raw = style_engine.validate_identity(message.text)
    except StyleValidationError as exc:
        await message.answer(f"❌ {escape(str(exc))}")
        return
    await state.update_data(style_alias_raw=raw)
    await state.set_state(StyleAdminStates.waiting_for_alias_display)
    await message.answer("متن نمایشی جدید را ارسال کنید. این مقدار جای شناسه خام در DB را نمی‌گیرد.")


@style_admin_router.message(StyleAdminStates.waiting_for_alias_display, F.text)
async def style_alias_display_input(message: Message, state: FSMContext):
    if not _authorized(message.from_user.id):
        return
    data = await state.get_data()
    scope = data.get("style_alias_scope")
    raw = data.get("style_alias_raw")
    try:
        display = style_engine.validate_display_text(message.text)
        if not scope or not raw:
            raise StyleValidationError("وضعیت ثبت منقضی شده است.")
        await style_engine.set_text_override(scope, raw, display)
    except StyleValidationError as exc:
        await message.answer(f"❌ {escape(str(exc))}")
        return
    await state.clear()
    await message.answer("✅ Text Override ذخیره شد. /style")


@style_admin_router.callback_query(F.data.startswith("style:alias:"))
async def style_alias_detail(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    item_id = _parse_numeric_callback(callback)
    item = await style_engine.get_override_by_id(item_id) if item_id is not None else None
    if not item:
        await callback.answer("آیتم پیدا نشد", show_alert=True)
        return
    await state.clear()
    await callback.message.edit_text(
        "📝 <b>Text Override</b>\n\n"
        f"Scope: <code>{escape(item.scope)}</code>\n"
        f"Raw: <code>{escape(item.raw_identity)}</code>\n"
        f"Visible: {escape(item.display_text)}\n\n"
        "هویت خام در منطق برنامه همچنان بدون تغییر باقی می‌ماند.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [await style_engine.styled_button("حذف Override", fallback="🗑", callback_data=f"style:aliasdelete:{item.id}")],
            [await style_engine.styled_button("بازگشت", icon_key="back", fallback="⬅️", callback_data="style:aliases")],
        ]),
    )
    await callback.answer()


@style_admin_router.callback_query(F.data.startswith("style:aliasdelete:"))
async def style_alias_delete(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    item_id = _parse_numeric_callback(callback)
    if item_id is None or not await style_engine.remove_text_override(item_id):
        await callback.answer("آیتم پیدا نشد", show_alert=True)
        return
    await state.clear()
    await _render_aliases(callback.message)
    await callback.answer("حذف شد")


@style_admin_router.callback_query(F.data == "style:cancel")
async def style_cancel(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    await state.clear()
    await _render_menu(callback.message)
    await callback.answer("لغو شد")
