from __future__ import annotations

from html import escape

import aiosqlite
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

import config
from premium_ui_service import PremiumUIError, premium_ui_service
from style_engine import style_engine


premium_ui_admin_router = Router(name="premium_ui_admin")


MESSAGE_TITLES = {
    "welcome_sudo": "خوش‌آمدگویی SUDO",
    "welcome_admin": "خوش‌آمدگویی ادمین",
    "unauthorized": "عدم دسترسی",
    "admin_added": "افزودن ادمین",
    "admin_removed": "حذف/غیرفعال‌سازی پنل",
    "admin_activated": "فعال‌سازی ادمین",
    "admin_deactivated": "غیرفعال‌سازی ادمین",
    "admin_not_found": "ادمین پیدا نشد",
    "panel_not_found": "پنل پیدا نشد",
    "invalid_format": "فرمت نامعتبر",
    "api_error": "خطای API",
    "database_error": "خطای دیتابیس",
    "limit_warning": "هشدار محدودیت",
    "limit_exceeded": "اتمام محدودیت",
    "users_reactivated": "فعال‌سازی کاربران",
    "admin_reactivated": "فعال‌سازی مجدد ادمین",
    "admin_users_deactivated": "غیرفعال‌سازی کاربران ادمین",
    "admin_password_randomized": "تغییر رمز ادمین",
    "no_deactivated_admins": "همه ادمین‌ها فعال‌اند",
    "select_admin_to_reactivate": "انتخاب ادمین برای فعال‌سازی",
    "select_panel_to_deactivate": "انتخاب پنل برای غیرفعال‌سازی",
    "select_panel_to_edit": "انتخاب پنل برای ویرایش",
    "panel_limits_updated": "بروزرسانی محدودیت پنل",
    "public_payment_instructions": "راهنمای پرداخت",
    "public_order_registered": "ثبت سفارش",
    "public_send_payment_note": "درخواست اطلاعات پرداخت",
    "public_send_receipt": "درخواست عکس رسید",
    "order_submitted_to_admin": "ارسال سفارش برای بررسی",
    "order_approved_user": "صدور پنل برای کاربر",
    "order_rejected_user": "رد سفارش",
    "login_url_updated": "ثبت آدرس ورود",
    "backup_created": "ساخت بکاپ",
    "backup_failed": "خطای بکاپ",
    "backup_schedule_saved": "ذخیره زمان‌بندی بکاپ",
    "backup_schedule_disabled": "غیرفعال شدن زمان‌بندی بکاپ",
}


class ButtonEditStates(StatesGroup):
    text = State()


class MessageEditStates(StatesGroup):
    body = State()


def _sudo(user_id: int) -> bool:
    return int(user_id) in config.SUDO_ADMINS


async def _deny(callback: CallbackQuery) -> bool:
    if _sudo(callback.from_user.id):
        return False
    await callback.answer("غیرمجاز", show_alert=True)
    return True


async def _btn(text: str, callback_data: str, *, icon_key: str | None = None, fallback: str | None = None):
    return await style_engine.styled_button(
        text,
        callback_data=callback_data,
        icon_key=icon_key,
        fallback=fallback,
    )


async def _purge_editor_buttons() -> None:
    # Internal editor controls should not clutter the selectable bot-button catalog.
    await premium_ui_service.ensure_schema()
    async with aiosqlite.connect(premium_ui_service.db_path) as conn:
        await conn.execute("DELETE FROM styled_button_catalog WHERE callback_data LIKE 'pui:%'")
        await conn.commit()


async def _render_buttons(message: Message, page: int = 0) -> None:
    await _purge_editor_buttons()
    items, pages = await premium_ui_service.list_buttons(page=page, page_size=12)
    items = [item for item in items if not item.callback_data.startswith("pui:")]
    rows = []
    lines = [
        "🔘 <b>متن و ایموجی دکمه‌های ربات</b>",
        "",
        "دکمه‌ای را انتخاب کنید؛ متن نمایشی و Premium Emoji آن را می‌توانید جداگانه تغییر دهید.",
        "Callback اصلی هیچ‌وقت تغییر نمی‌کند.",
        "",
    ]
    if not items:
        lines.append("هنوز دکمه‌ای در کاتالوگ ثبت نشده. یک‌بار منوهای ربات را باز کنید و دوباره برگردید.")
    for item in items:
        label = item.display_text or item.default_text
        emoji = f" · ✨ {item.emoji_key}" if item.emoji_key else ""
        rows.append([await _btn(f"{label}{emoji}"[:55], f"pui:b:{item.id}", fallback="🔘")])
    if pages > 1:
        nav = []
        if page > 0:
            nav.append(await _btn("قبلی", f"pui:bs:{page-1}", fallback="⬅️"))
        nav.append(await _btn(f"{page+1}/{pages}", "pui:noop", fallback="📄"))
        if page + 1 < pages:
            nav.append(await _btn("بعدی", f"pui:bs:{page+1}", fallback="➡️"))
        rows.append(nav)
    rows.extend([
        [await _btn("Premium Emojiها", "style:emojis", icon_key="style", fallback="✨")],
        [await _btn("خانه", "back_to_main", icon_key="home", fallback="🏠")],
    ])
    await message.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@premium_ui_admin_router.callback_query(F.data == "cc:buttons")
async def buttons_root(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    await state.clear()
    await _render_buttons(callback.message, 0)
    await callback.answer()


@premium_ui_admin_router.callback_query(F.data.startswith("pui:bs:"))
async def buttons_page(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    await state.clear()
    try:
        page = int((callback.data or "").rsplit(":", 1)[-1])
    except ValueError:
        page = 0
    await _render_buttons(callback.message, page)
    await callback.answer()


@premium_ui_admin_router.callback_query(F.data == "pui:noop")
async def noop(callback: CallbackQuery):
    await callback.answer()


async def _render_button_detail(message: Message, item_id: int) -> None:
    item = await premium_ui_service.get_button(item_id)
    if not item:
        await message.edit_text("❌ دکمه پیدا نشد.")
        return
    visible = item.display_text or item.default_text
    emoji_key = item.emoji_key or item.default_icon_key or "—"
    text = (
        "🔘 <b>ویرایش دکمه</b>\n\n"
        f"متن فعلی: <b>{escape(visible)}</b>\n"
        f"متن اصلی: {escape(item.default_text)}\n"
        f"ایموجی: <code>{escape(str(emoji_key))}</code>\n"
        f"Callback: <code>{escape(item.callback_data)}</code>"
    )
    rows = [
        [await _btn("تغییر متن", f"pui:bt:{item.id}", fallback="✏️")],
        [await _btn("انتخاب Premium Emoji", f"pui:be:{item.id}", fallback="✨")],
        [
            await _btn("ریست متن", f"pui:btr:{item.id}", fallback="♻️"),
            await _btn("ریست ایموجی", f"pui:ber:{item.id}", fallback="♻️"),
        ],
        [await _btn("بازگشت", "cc:buttons", icon_key="back", fallback="⬅️")],
    ]
    await message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@premium_ui_admin_router.callback_query(F.data.startswith("pui:b:"))
async def button_detail(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    await state.clear()
    try:
        item_id = int((callback.data or "").rsplit(":", 1)[-1])
    except ValueError:
        await callback.answer("نامعتبر", show_alert=True)
        return
    await _render_button_detail(callback.message, item_id)
    await callback.answer()


@premium_ui_admin_router.callback_query(F.data.startswith("pui:bt:"))
async def button_text_start(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    try:
        item_id = int((callback.data or "").rsplit(":", 1)[-1])
    except ValueError:
        await callback.answer("نامعتبر", show_alert=True)
        return
    if not await premium_ui_service.get_button(item_id):
        await callback.answer("پیدا نشد", show_alert=True)
        return
    await state.clear()
    await state.update_data(pui_button_id=item_id)
    await state.set_state(ButtonEditStates.text)
    await callback.message.answer("متن جدید دکمه را بفرستید؛ حداکثر ۶۴ کاراکتر و یک‌خطی.")
    await callback.answer()


@premium_ui_admin_router.message(ButtonEditStates.text, F.text)
async def button_text_value(message: Message, state: FSMContext):
    if not _sudo(message.from_user.id):
        return
    data = await state.get_data()
    item_id = int(data.get("pui_button_id") or 0)
    try:
        await premium_ui_service.set_button_text(item_id, message.text or "")
    except PremiumUIError as exc:
        await message.answer(f"❌ {escape(str(exc))}")
        return
    await state.clear()
    await message.answer("✅ متن دکمه ذخیره شد.")


@premium_ui_admin_router.callback_query(F.data.startswith("pui:btr:"))
async def button_text_reset(callback: CallbackQuery):
    if await _deny(callback):
        return
    item_id = int((callback.data or "").rsplit(":", 1)[-1])
    try:
        await premium_ui_service.reset_button_text(item_id)
    except PremiumUIError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await _render_button_detail(callback.message, item_id)
    await callback.answer("متن ریست شد")


@premium_ui_admin_router.callback_query(F.data.startswith("pui:be:"))
async def button_emoji_menu(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    await state.clear()
    item_id = int((callback.data or "").rsplit(":", 1)[-1])
    if not await premium_ui_service.get_button(item_id):
        await callback.answer("پیدا نشد", show_alert=True)
        return
    emojis = [item for item in await style_engine.list_emojis() if item.enabled]
    rows = []
    for emoji in emojis:
        preview = await style_engine.render_emoji(emoji.key, fallback=emoji.fallback_unicode)
        rows.append([await _btn(f"{emoji.fallback_unicode} {emoji.key}", f"pui:bes:{item_id}:{emoji.id}")])
    rows.append([await _btn("بدون ایموجی سفارشی", f"pui:ber:{item_id}", fallback="🚫")])
    rows.append([await _btn("بازگشت", f"pui:b:{item_id}", icon_key="back", fallback="⬅️")])
    text = "✨ <b>انتخاب Premium Emoji</b>\n\n"
    text += "هنوز ایموجی ثبت نشده؛ اول از بخش Premium Emojis یک مورد اضافه کنید." if not emojis else "ایموجی موردنظر را انتخاب کنید:"
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


@premium_ui_admin_router.callback_query(F.data.startswith("pui:bes:"))
async def button_emoji_select(callback: CallbackQuery):
    if await _deny(callback):
        return
    parts = (callback.data or "").split(":")
    if len(parts) != 4:
        await callback.answer("نامعتبر", show_alert=True)
        return
    item_id, emoji_id = int(parts[2]), int(parts[3])
    emoji = await style_engine.get_emoji_by_id(emoji_id)
    if not emoji:
        await callback.answer("ایموجی پیدا نشد", show_alert=True)
        return
    try:
        await premium_ui_service.set_button_emoji(item_id, emoji.key)
    except PremiumUIError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await _render_button_detail(callback.message, item_id)
    await callback.answer("ایموجی ذخیره شد")


@premium_ui_admin_router.callback_query(F.data.startswith("pui:ber:"))
async def button_emoji_reset(callback: CallbackQuery):
    if await _deny(callback):
        return
    item_id = int((callback.data or "").rsplit(":", 1)[-1])
    try:
        await premium_ui_service.reset_button_emoji(item_id)
    except PremiumUIError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await _render_button_detail(callback.message, item_id)
    await callback.answer("ایموجی ریست شد")


async def _render_messages(message: Message) -> None:
    items = await premium_ui_service.list_messages()
    rows = []
    lines = [
        "📝 <b>متن‌های ربات + Premium Emoji</b>",
        "",
        "داخل هر متن می‌توانید از این فرمت استفاده کنید:",
        "<code>{emoji:wire}</code>",
        "",
        "اگر کلید <code>wire</code> را در Premium Emojis تعریف کرده باشید، هنگام ارسال خودکار با همان Premium Emoji جایگزین می‌شود.",
    ]
    for item in items:
        title = MESSAGE_TITLES.get(item.key, item.key)
        state = "✨" if item.is_overridden else "▫️"
        rows.append([await _btn(f"{state} {title}"[:55], f"pui:m:{item.key}", fallback="📝")])
    rows.extend([
        [await _btn("راهنمای {emoji:key}", "pui:mh", fallback="❔")],
        [await _btn("Premium Emojiها", "style:emojis", fallback="✨")],
        [await _btn("خانه", "back_to_main", icon_key="home", fallback="🏠")],
    ])
    await message.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@premium_ui_admin_router.callback_query(F.data == "cc:texts")
async def messages_root(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    await state.clear()
    await _render_messages(callback.message)
    await callback.answer()


@premium_ui_admin_router.callback_query(F.data == "pui:mh")
async def message_help(callback: CallbackQuery):
    if await _deny(callback):
        return
    await callback.message.edit_text(
        "❔ <b>قالب Premium Emoji</b>\n\n"
        "مثال:\n"
        "<code>{emoji:wire} اتصال شما آماده است</code>\n\n"
        "اگر Premium Emoji با Key برابر <code>wire</code> ثبت شده باشد، موقع ارسال تگ با ایموجی واقعی جایگزین می‌شود.\n\n"
        "می‌توانید چند مورد در یک متن استفاده کنید:\n"
        "<code>{emoji:success} موفق\n{emoji:warning} هشدار</code>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            await _btn("بازگشت", "cc:texts", icon_key="back", fallback="⬅️")
        ]]),
    )
    await callback.answer()


async def _render_message_detail(message: Message, key: str) -> None:
    item = await premium_ui_service.get_message(key)
    if not item:
        await message.edit_text("❌ متن پیدا نشد.")
        return
    title = MESSAGE_TITLES.get(key, key)
    status = "✨ سفارشی" if item.is_overridden else "▫️ پیش‌فرض"
    preview_source = item.body
    text = (
        f"📝 <b>{escape(title)}</b>\n\n"
        f"Key: <code>{escape(key)}</code>\n"
        f"وضعیت: {status}\n\n"
        "متن فعلی:\n"
        f"<pre>{escape(preview_source)}</pre>"
    )
    rows = [
        [await _btn("ویرایش متن", f"pui:me:{key}", fallback="✏️")],
        [await _btn("پیش‌نمایش واقعی", f"pui:mp:{key}", fallback="👁")],
        [await _btn("بازگردانی متن اصلی", f"pui:mr:{key}", fallback="♻️")],
        [await _btn("بازگشت", "cc:texts", icon_key="back", fallback="⬅️")],
    ]
    await message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@premium_ui_admin_router.callback_query(F.data.startswith("pui:m:"))
async def message_detail(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    await state.clear()
    key = (callback.data or "").split(":", 2)[-1]
    await _render_message_detail(callback.message, key)
    await callback.answer()


@premium_ui_admin_router.callback_query(F.data.startswith("pui:me:"))
async def message_edit_start(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    key = (callback.data or "").split(":", 2)[-1]
    if not await premium_ui_service.get_message(key):
        await callback.answer("پیدا نشد", show_alert=True)
        return
    await state.clear()
    await state.update_data(pui_message_key=key)
    await state.set_state(MessageEditStates.body)
    await callback.message.answer(
        "متن جدید را بفرستید.\n\n"
        "برای Premium Emoji بنویسید: <code>{emoji:wire}</code>\n"
        "متغیرهای موجود مثل <code>{username}</code> و <code>{password}</code> را اگر در متن فعلی وجود دارند حذف نکنید."
    )
    await callback.answer()


@premium_ui_admin_router.message(MessageEditStates.body, F.text)
async def message_edit_value(message: Message, state: FSMContext):
    if not _sudo(message.from_user.id):
        return
    data = await state.get_data()
    key = str(data.get("pui_message_key") or "")
    try:
        await premium_ui_service.set_message(key, message.text or "")
    except PremiumUIError as exc:
        await message.answer(f"❌ {escape(str(exc))}")
        return
    await state.clear()
    await message.answer("✅ متن ذخیره شد و از همین الان در ارسال‌های ربات استفاده می‌شود.")


@premium_ui_admin_router.callback_query(F.data.startswith("pui:mr:"))
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
    await callback.answer("متن اصلی برگشت")


@premium_ui_admin_router.callback_query(F.data.startswith("pui:mp:"))
async def message_preview(callback: CallbackQuery):
    if await _deny(callback):
        return
    key = (callback.data or "").split(":", 2)[-1]
    item = await premium_ui_service.get_message(key)
    if not item:
        await callback.answer("پیدا نشد", show_alert=True)
        return
    rendered = await premium_ui_service.render_placeholders(item.body)
    # Preview only; unresolved business-format variables are shown literally.
    try:
        await callback.message.answer(rendered)
    except Exception:
        await callback.message.answer(
            "❌ پیش‌نمایش HTML این متن معتبر نیست. متن خام:\n<pre>" + escape(item.body) + "</pre>"
        )
    await callback.answer()
