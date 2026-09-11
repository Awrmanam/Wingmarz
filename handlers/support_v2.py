from __future__ import annotations

from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

import config
from authorization import is_staff
from database import db
from style_engine import style_engine
import support_service
from handlers.control_center import TicketCreateStates, TicketReplyStates


support_v2_router = Router(name="support_v2")
PAGE_SIZE = 8


async def _button(text: str, callback_data: str, *, icon_key: str | None = None, fallback: str | None = None):
    return await style_engine.styled_button(
        text,
        callback_data=callback_data,
        icon_key=icon_key,
        fallback=fallback,
    )


async def _home_callback(user_id: int) -> str:
    if is_staff(int(user_id)):
        return "back_to_main"
    return "back_to_admin_main" if await db.is_admin_authorized(int(user_id)) else "public_back_main"


async def _support_back(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        await _button("⬅️ بازگشت", await _home_callback(user_id), icon_key="back", fallback="⬅️")
    ]])


async def _render_support_home(message: Message, user_id: int) -> None:
    rows = [
        [await _button("✉️ ایجاد تیکت", "support:new", icon_key="support", fallback="✉️")],
        [await _button("🎫 تیکت‌های من", "support:mine", icon_key="ticket", fallback="🎫")],
        [await _button("⬅️ بازگشت", await _home_callback(user_id), icon_key="back", fallback="⬅️")],
    ]
    await message.edit_text(
        config.MESSAGES.get("support_home", "🎧 <b>پشتیبانی</b>"),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@support_v2_router.callback_query(F.data == "support:home")
async def support_home(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await _render_support_home(callback.message, callback.from_user.id)
    await callback.answer()


@support_v2_router.callback_query(F.data == "support:new")
async def support_new(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(TicketCreateStates.waiting_for_subject)
    await callback.message.edit_text(
        config.MESSAGES.get("support_subject", "📝 عنوان کوتاه درخواست را ارسال کنید:"),
        reply_markup=await _support_back(callback.from_user.id),
    )
    await callback.answer()


@support_v2_router.message(TicketCreateStates.waiting_for_subject, F.text)
async def ticket_subject_value(message: Message, state: FSMContext):
    subject = (message.text or "").strip()
    if not subject or len(subject) > 120:
        await message.answer("⚠️ عنوان باید بین ۱ تا ۱۲۰ کاراکتر باشد.")
        return
    await state.update_data(ticket_subject=subject)
    await state.set_state(TicketCreateStates.waiting_for_body)
    await message.answer(
        config.MESSAGES.get("support_body", "📝 متن درخواست را ارسال کنید:"),
        reply_markup=await _support_back(message.from_user.id),
    )


@support_v2_router.message(TicketCreateStates.waiting_for_body, F.text)
async def ticket_body_value(message: Message, state: FSMContext):
    body = (message.text or "").strip()
    data = await state.get_data()
    subject = str(data.get("ticket_subject") or "").strip()
    if not subject or not body or len(body) > 3500:
        await message.answer("⚠️ متن تیکت نامعتبر است.")
        return
    ticket_id = await support_service.create_ticket(message.from_user.id, subject, body)
    await state.clear()
    rows = [
        [await _button("🎫 مشاهده تیکت", f"support:ticket:{ticket_id}", fallback="🎫")],
        [await _button("🔒 بستن تیکت", f"support:closeask:{ticket_id}", fallback="🔒")],
        [await _button("🎧 پشتیبانی", "support:home", fallback="🎧")],
    ]
    await message.answer(
        config.MESSAGES.get("support_submitted", "✅ درخواست شما ثبت شد."),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    for sudo_id in config.SUDO_ADMINS:
        try:
            await message.bot.send_message(
                int(sudo_id),
                f"🎧 <b>تیکت جدید #{ticket_id}</b>\n👤 <code>{message.from_user.id}</code>\n📝 عنوان: {escape(subject)}",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    await _button("🎫 مشاهده تیکت", f"cc:ticket:{ticket_id}", fallback="🎫")
                ]]),
            )
        except Exception:
            pass


@support_v2_router.callback_query(F.data == "support:mine")
async def my_tickets_home(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    rows = [
        [
            await _button("🟢 تیکت‌های باز", "support:mine:open:0", fallback="🟢"),
            await _button("🔒 تیکت‌های بسته", "support:mine:closed:0", fallback="🔒"),
        ],
        [await _button("⬅️ بازگشت", "support:home", icon_key="back", fallback="⬅️")],
    ]
    await callback.message.edit_text(
        "🎫 <b>تیکت‌های من</b>\n\nیکی از بخش‌های زیر را انتخاب کنید:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


async def _render_my_tickets(message: Message, user_id: int, status: str, page: int) -> None:
    items, total, page, pages = await support_service.list_user_tickets(
        user_id, status=status, page=page, page_size=PAGE_SIZE
    )
    title = "🟢 تیکت‌های باز" if status == "open" else "🔒 تیکت‌های بسته"
    lines = [f"{title}", "", f"تعداد: <b>{total}</b>"]
    rows = []
    for item in items:
        icon = "🟢" if status == "open" else "🔒"
        subject = escape(str(item.get("subject") or "تیکت"))
        lines.append(f"{icon} <b>#{item['id']}</b> · {subject}")
        rows.append([await _button(f"🎫 تیکت #{item['id']} · {str(item.get('subject') or '')[:28]}", f"support:ticket:{item['id']}", fallback="🎫")])
    nav = []
    if page > 0:
        nav.append(await _button("⬅️ قبلی", f"support:mine:{status}:{page-1}", fallback="⬅️"))
    if page + 1 < pages:
        nav.append(await _button("بعدی ➡️", f"support:mine:{status}:{page+1}", fallback="➡️"))
    if nav:
        rows.append(nav)
    rows.extend([
        [await _button("🎫 تیکت‌های من", "support:mine", fallback="🎫")],
        [await _button("🎧 پشتیبانی", "support:home", fallback="🎧")],
    ])
    await message.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@support_v2_router.callback_query(F.data.startswith("support:mine:open:") | F.data.startswith("support:mine:closed:"))
async def my_tickets_list(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    parts = (callback.data or "").split(":")
    status = "closed" if len(parts) > 2 and parts[2] == "closed" else "open"
    try:
        page = int(parts[-1])
    except ValueError:
        page = 0
    await _render_my_tickets(callback.message, callback.from_user.id, status, page)
    await callback.answer()


async def _render_user_ticket(message: Message, user_id: int, ticket_id: int) -> bool:
    ticket = await support_service.get_ticket(ticket_id, user_id=user_id)
    if not ticket:
        return False
    is_open = ticket.get("status") == "open"
    status_text = "🟢 باز" if is_open else "🔒 بسته"
    text = (
        f"🎫 <b>تیکت #{ticket['id']}</b>\n\n"
        f"📝 عنوان: {escape(str(ticket.get('subject') or '-'))}\n"
        f"📌 وضعیت: <b>{status_text}</b>\n\n"
        f"💬 <b>پیام شما:</b>\n{escape(str(ticket.get('body') or ''))}"
    )
    if ticket.get("admin_reply"):
        text += f"\n\n🎧 <b>آخرین پاسخ پشتیبانی:</b>\n{escape(str(ticket['admin_reply']))}"
    rows = []
    if is_open:
        rows.append([await _button("🔒 بستن تیکت", f"support:closeask:{ticket_id}", fallback="🔒")])
    rows.extend([
        [await _button("🎫 تیکت‌های من", "support:mine", fallback="🎫")],
        [await _button("⬅️ بازگشت به پشتیبانی", "support:home", icon_key="back", fallback="⬅️")],
    ])
    await message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    return True


@support_v2_router.callback_query(F.data.startswith("support:ticket:"))
async def user_ticket_detail(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        ticket_id = int((callback.data or "").rsplit(":", 1)[-1])
    except ValueError:
        await callback.answer("⚠️ تیکت نامعتبر است.", show_alert=True)
        return
    if not await _render_user_ticket(callback.message, callback.from_user.id, ticket_id):
        await callback.answer("⛔ این تیکت متعلق به شما نیست یا پیدا نشد.", show_alert=True)
        return
    await callback.answer()


@support_v2_router.callback_query(F.data.startswith("support:closeask:"))
async def user_close_ask(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        ticket_id = int((callback.data or "").rsplit(":", 1)[-1])
    except ValueError:
        await callback.answer("⚠️ نامعتبر", show_alert=True)
        return
    ticket = await support_service.get_ticket(ticket_id, user_id=callback.from_user.id)
    if not ticket or ticket.get("status") != "open":
        await callback.answer("🔒 این تیکت قبلاً بسته شده یا پیدا نشد.", show_alert=True)
        return
    rows = [[
        await _button("✅ بله، بسته شود", f"support:closeconfirm:{ticket_id}", fallback="✅"),
        await _button("↩️ انصراف", f"support:ticket:{ticket_id}", fallback="↩️"),
    ]]
    await callback.message.edit_text(
        f"⚠️ <b>بستن تیکت #{ticket_id}</b>\n\nآیا مطمئن هستید؟\nپس از بسته شدن، این تیکت دوباره باز نمی‌شود.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@support_v2_router.callback_query(F.data.startswith("support:closeconfirm:"))
async def user_close_confirm(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        ticket_id = int((callback.data or "").rsplit(":", 1)[-1])
    except ValueError:
        await callback.answer("⚠️ نامعتبر", show_alert=True)
        return
    closed = await support_service.close_ticket(
        ticket_id, callback.from_user.id, expected_user_id=callback.from_user.id
    )
    if not closed:
        await callback.answer("🔒 تیکت قبلاً بسته شده یا پیدا نشد.", show_alert=True)
        return
    for sudo_id in config.SUDO_ADMINS:
        try:
            await callback.bot.send_message(
                int(sudo_id),
                f"🔒 کاربر تیکت <b>#{ticket_id}</b> را بست.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    await _button("🎫 مشاهده تیکت", f"cc:ticket:{ticket_id}", fallback="🎫")
                ]]),
            )
        except Exception:
            pass
    await _render_user_ticket(callback.message, callback.from_user.id, ticket_id)
    await callback.answer("✅ تیکت بسته شد")


@support_v2_router.callback_query(F.data.startswith("cc:ticketclose:"))
async def admin_close_ask(callback: CallbackQuery, state: FSMContext):
    if not is_staff(callback.from_user.id):
        await callback.answer("⛔ غیرمجاز", show_alert=True)
        return
    await state.clear()
    try:
        ticket_id = int((callback.data or "").rsplit(":", 1)[-1])
    except ValueError:
        await callback.answer("⚠️ نامعتبر", show_alert=True)
        return
    ticket = await support_service.get_ticket(ticket_id)
    if not ticket or ticket.get("status") != "open":
        await callback.answer("🔒 این تیکت قبلاً بسته شده یا پیدا نشد.", show_alert=True)
        return
    rows = [[
        await _button("✅ بله، بسته شود", f"cc:ticketcloseconfirm:{ticket_id}", fallback="✅"),
        await _button("↩️ انصراف", f"cc:ticket:{ticket_id}", fallback="↩️"),
    ]]
    await callback.message.edit_text(
        f"⚠️ <b>بستن تیکت #{ticket_id}</b>\n\nاز بستن این تیکت مطمئن هستید؟\nاین عملیات قابل بازگشت نیست.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@support_v2_router.callback_query(F.data.startswith("cc:ticketcloseconfirm:"))
async def admin_close_confirm(callback: CallbackQuery, state: FSMContext):
    if not is_staff(callback.from_user.id):
        await callback.answer("⛔ غیرمجاز", show_alert=True)
        return
    await state.clear()
    try:
        ticket_id = int((callback.data or "").rsplit(":", 1)[-1])
    except ValueError:
        await callback.answer("⚠️ نامعتبر", show_alert=True)
        return
    closed = await support_service.close_ticket(ticket_id, callback.from_user.id)
    if not closed:
        await callback.answer("🔒 تیکت قبلاً بسته شده یا پیدا نشد.", show_alert=True)
        return
    recipient = int(closed["user_id"])
    try:
        await callback.bot.send_message(
            recipient,
            f"🔒 <b>تیکت #{ticket_id} بسته شد</b>\n\nاین تیکت توسط پشتیبانی بسته شده و دوباره باز نمی‌شود.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                await _button("🎫 مشاهده تیکت", f"support:ticket:{ticket_id}", fallback="🎫")
            ]]),
        )
    except Exception:
        pass
    # The existing admin detail handler remains the canonical renderer.
    callback.data = f"cc:ticket:{ticket_id}"
    from handlers.control_center import control_center_ticket_detail
    await control_center_ticket_detail(callback, state)


@support_v2_router.message(TicketReplyStates.waiting_for_reply, F.text)
async def admin_reply_value(message: Message, state: FSMContext):
    if not is_staff(message.from_user.id):
        return
    data = await state.get_data()
    ticket_id = data.get("ticket_reply_id")
    reply = (message.text or "").strip()
    if not ticket_id or not reply or len(reply) > 3500:
        await message.answer("⚠️ پاسخ نامعتبر است.")
        return
    recipient = await support_service.reply_ticket(int(ticket_id), message.from_user.id, reply)
    if recipient is None:
        await state.clear()
        await message.answer("🔒 این تیکت بسته شده یا پیدا نشد.")
        return
    delivered = True
    try:
        await message.bot.send_message(
            recipient,
            config.MESSAGES.get("support_reply", "🎧 <b>پاسخ پشتیبانی</b>\n\n{reply}").format(reply=escape(reply)),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                await _button("🎫 مشاهده تیکت", f"support:ticket:{int(ticket_id)}", fallback="🎫")
            ]]),
        )
    except Exception:
        delivered = False
    await state.clear()
    result_text = "✅ پاسخ ارسال شد. تیکت باز می‌ماند." if delivered else "⚠️ پاسخ ذخیره شد اما ارسال پیام به کاربر ناموفق بود."
    await message.answer(
        result_text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            await _button("🎫 بازگشت به تیکت", f"cc:ticket:{int(ticket_id)}", fallback="🎫")
        ]]),
    )
