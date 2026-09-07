from __future__ import annotations

from html import escape
import math

import aiosqlite
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

import config
from authorization import is_staff
import support_service
from order_tracking import needs_attention
from order_queries import FILTERS, STATUS_LABELS, list_orders
from style_engine import style_engine


control_center_router = Router(name="control_center")
PAGE_SIZE = 8


class TicketCreateStates(StatesGroup):
    waiting_for_subject = State()
    waiting_for_body = State()


class TicketReplyStates(StatesGroup):
    waiting_for_reply = State()


def _is_sudo(user_id: int) -> bool:
    return is_staff(user_id)


async def _deny(callback: CallbackQuery) -> bool:
    if _is_sudo(callback.from_user.id):
        return False
    await callback.answer("غیرمجاز", show_alert=True)
    return True


async def _ensure_schema():
    await support_service.ensure_schema()


async def _button(text: str, callback_data: str, *, icon_key: str | None = None, fallback: str | None = None):
    return await style_engine.styled_button(
        text,
        icon_key=icon_key,
        fallback=fallback,
        callback_data=callback_data,
    )


async def build_control_center_keyboard():
    from handlers.operations import build_operational_dashboard_keyboard
    return await build_operational_dashboard_keyboard()


@control_center_router.message(Command("control"))
async def control_center_command(message: Message, state: FSMContext):
    if not _is_sudo(message.from_user.id):
        return
    from handlers.operations import _send_dashboard
    await state.clear()
    await _send_dashboard(message)


async def _order_count(conn: aiosqlite.Connection) -> int:
    try:
        async with conn.execute("SELECT COUNT(*) FROM orders") as cur:
            row = await cur.fetchone()
            return int((row or [0])[0] or 0)
    except aiosqlite.OperationalError:
        return 0


async def _render_orders(message, page, category="pending"):
    orders, total, page, pages = await list_orders(category, page, PAGE_SIZE)
    rows = [[await _button(title, f"cc:orders:{key}:0") for key, (title, _) in list(FILTERS.items())[i:i+2]]
            for i in range(0, len(FILTERS), 2)]
    for order in orders:
        label = str(order.get("plan_name_snapshot") or "سفارش")[:25]
        status = STATUS_LABELS.get(order['status'], 'در حال بررسی')
        rows.append([await _button(f"{label} · {status}", f"cc:order:{order['id']}")])
    nav = []
    if page:
        nav.append(await _button("قبلی", f"cc:orders:{category}:{page-1}"))
    if page+1 < pages:
        nav.append(await _button("بعدی", f"cc:orders:{category}:{page+1}"))
    if nav:
        rows.append(nav)
    rows.append([await _button("خانه", "back_to_main", fallback="🏠")])
    await message.edit_text(f"🧾 <b>{FILTERS[category][0]}</b>\n\nتعداد: {total}", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@control_center_router.callback_query(F.data.startswith("cc:orders:"))
async def control_center_orders(callback, state):
    if await _deny(callback):
        return
    parts = (callback.data or '').split(':')
    category = parts[2] if len(parts) == 4 else 'pending'
    if category not in FILTERS:
        await callback.answer("فیلتر نامعتبر", show_alert=True)
        return
    try:
        page = int(parts[-1])
    except ValueError:
        await callback.answer("صفحه نامعتبر", show_alert=True)
        return
    await state.clear()
    await _render_orders(callback.message, page, category)
    await callback.answer()


@control_center_router.callback_query(F.data.startswith("cc:order:"))
async def control_center_order_detail(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    await state.clear()
    try:
        order_id = int((callback.data or "").rsplit(":", 1)[-1])
    except ValueError:
        await callback.answer("شناسه نامعتبر", show_alert=True)
        return
    async with aiosqlite.connect(config.DATABASE_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        try:
            async with conn.execute("SELECT * FROM orders WHERE id=?", (order_id,)) as cur:
                row = await cur.fetchone()
        except aiosqlite.OperationalError:
            row = None
    if not row:
        await callback.answer("سفارش پیدا نشد", show_alert=True)
        return
    text = (
        f"🧾 <b>سفارش #{row['id']}</b>\n\n"
        f"👤 User ID: <code>{row['user_id']}</code>\n"
        f"📦 پلن: {escape(str(row['plan_name_snapshot'] or row['plan_id'] or '-'))}\n"
        f"🔁 نوع: {escape(str(row['order_type'] or '-'))}\n"
        f"💳 وضعیت: <b>{escape(STATUS_LABELS.get(row['status'], 'در حال بررسی'))}</b>\n"
        f"💵 مبلغ: <b>{int(row['price_snapshot'] or 0):,}</b>\n"
        f"🕒 ثبت: {escape(str(row['created_at'] or '-'))}\n"
        f"✅ تاییدکننده: {escape(str(row['approved_by'] or '-'))}\n"
        f"🧩 پنل صادرشده: {escape(str(row['issued_admin_id'] or '-'))}"
    )
    actions = []
    if row['status'] == 'submitted':
        actions.append([await _button("تأیید و صدور", f"order_approve_{order_id}", fallback="✅"),
                        await _button("رد سفارش", f"order_reject_{order_id}", fallback="❌")])
    if row['status'] in {'pending', 'submitted', 'failed'} and (row['status'] == 'failed' or row['rebecca_provision_state'] in {'failed', 'uncertain'} or await needs_attention(order_id)):
        actions.append([await _button("بررسی و تلاش دوباره", f"order_retry_{order_id}", fallback="🔁")])
    if row['receipt_file_id']:
        actions.append([await _button("مشاهده رسید", f"cc:receipt:{order_id}")])
    kb = InlineKeyboardMarkup(inline_keyboard=actions + [
        [await _button("بازگشت به سفارش‌ها", "cc:orders:0", icon_key="back", fallback="⬅️")],
        [await _button("خانه", "back_to_main", icon_key="home", fallback="🏠")],
    ])
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


async def _unique_user_ids(conn: aiosqlite.Connection) -> list[int]:
    try:
        async with conn.execute(
            """
            SELECT user_id FROM (
                SELECT user_id FROM admins
                UNION
                SELECT user_id FROM orders
            )
            ORDER BY user_id DESC
            """
        ) as cur:
            rows = await cur.fetchall()
        return [int(row[0]) for row in rows]
    except aiosqlite.OperationalError:
        return []


async def _render_users(message: Message, page: int) -> None:
    page = max(0, page)
    async with aiosqlite.connect(config.DATABASE_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        user_ids = await _unique_user_ids(conn)
        total = len(user_ids)
        pages = max(1, math.ceil(total / PAGE_SIZE))
        page = min(page, pages - 1)
        selected = user_ids[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
        records = []
        for user_id in selected:
            async with conn.execute(
                "SELECT username, first_name, last_name FROM admins WHERE user_id=? ORDER BY id DESC LIMIT 1",
                (user_id,),
            ) as cur:
                profile = await cur.fetchone()
            async with conn.execute("SELECT COUNT(*) FROM admins WHERE user_id=?", (user_id,)) as cur:
                admin_count = int((await cur.fetchone())[0])
            async with conn.execute("SELECT COUNT(*) FROM orders WHERE user_id=?", (user_id,)) as cur:
                order_count = int((await cur.fetchone())[0])
            records.append((user_id, profile, admin_count, order_count))

    lines = ["👥 <b>کاربران</b>", "", f"کاربران شناخته‌شده: <b>{total}</b>"]
    buttons = []
    for user_id, profile, admin_count, order_count in records:
        display = ""
        if profile:
            name = " ".join(filter(None, [profile["first_name"], profile["last_name"]])).strip()
            display = name or (f"@{profile['username']}" if profile["username"] else "")
        lines.append(f"• <code>{user_id}</code> {escape(display)} · پنل {admin_count} · سفارش {order_count}")
        buttons.append([await _button(str(user_id), f"cc:user:{user_id}", fallback="👤")])
    nav = []
    if page > 0:
        nav.append(await _button("قبلی", f"cc:users:{page-1}", fallback="⬅️"))
    if page + 1 < pages:
        nav.append(await _button("بعدی", f"cc:users:{page+1}", fallback="➡️"))
    if nav:
        buttons.append(nav)
    buttons.append([await _button("خانه", "back_to_main", icon_key="home", fallback="🏠")])
    await message.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@control_center_router.callback_query(F.data.startswith("cc:users:"))
async def control_center_users(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    await state.clear()
    try:
        page = int((callback.data or "").rsplit(":", 1)[-1])
    except ValueError:
        page = 0
    await _render_users(callback.message, page)
    await callback.answer()


@control_center_router.callback_query(F.data.startswith("cc:user:"))
async def control_center_user_detail(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    await state.clear()
    try:
        user_id = int((callback.data or "").rsplit(":", 1)[-1])
    except ValueError:
        await callback.answer("شناسه نامعتبر", show_alert=True)
        return
    async with aiosqlite.connect(config.DATABASE_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute("SELECT COUNT(*) FROM admins WHERE user_id=?", (user_id,)) as cur:
            admin_count = int((await cur.fetchone())[0])
        async with conn.execute("SELECT COUNT(*) FROM orders WHERE user_id=?", (user_id,)) as cur:
            order_count = int((await cur.fetchone())[0])
        async with conn.execute(
            "SELECT COALESCE(SUM(price_snapshot),0) FROM orders WHERE user_id=? AND status IN ('approved','completed')",
            (user_id,),
        ) as cur:
            paid = int((await cur.fetchone())[0] or 0)
        async with conn.execute(
            "SELECT username, first_name, last_name FROM admins WHERE user_id=? ORDER BY id DESC LIMIT 1",
            (user_id,),
        ) as cur:
            profile = await cur.fetchone()
    name = "-"
    username = "-"
    if profile:
        name = " ".join(filter(None, [profile["first_name"], profile["last_name"]])).strip() or "-"
        username = f"@{profile['username']}" if profile["username"] else "-"
    text = (
        "👤 <b>پروفایل کاربر</b>\n\n"
        f"ID: <code>{user_id}</code>\n"
        f"نام: {escape(name)}\n"
        f"Username: {escape(username)}\n"
        f"🧩 پنل‌ها: <b>{admin_count}</b>\n"
        f"🧾 سفارش‌ها: <b>{order_count}</b>\n"
        f"💵 خرید تاییدشده: <b>{paid:,}</b>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [await _button("بازگشت", "cc:users:0", icon_key="back", fallback="⬅️")],
        [await _button("خانه", "back_to_main", icon_key="home", fallback="🏠")],
    ])
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


@control_center_router.callback_query(F.data == "cc:stats")
async def control_center_stats(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    await state.clear()
    async with aiosqlite.connect(config.DATABASE_PATH) as conn:
        stats = {}
        for key, sql in {
            "orders": "SELECT COUNT(*) FROM orders",
            "pending": "SELECT COUNT(*) FROM orders WHERE status='pending'",
            "approved": "SELECT COUNT(*) FROM orders WHERE status IN ('approved','completed')",
            "revenue": "SELECT COALESCE(SUM(price_snapshot),0) FROM orders WHERE status IN ('approved','completed')",
            "admins": "SELECT COUNT(*) FROM admins",
            "active_admins": "SELECT COUNT(*) FROM admins WHERE is_active=1",
            "plans": "SELECT COUNT(*) FROM plans",
            "active_plans": "SELECT COUNT(*) FROM plans WHERE is_active=1",
        }.items():
            try:
                async with conn.execute(sql) as cur:
                    stats[key] = int((await cur.fetchone())[0] or 0)
            except aiosqlite.OperationalError:
                stats[key] = 0
    text = (
        "📊 <b>آمار و گزارشات</b>\n\n"
        f"🧾 کل سفارش‌ها: <b>{stats['orders']}</b>\n"
        f"⏳ در انتظار: <b>{stats['pending']}</b>\n"
        f"✅ تایید/تکمیل: <b>{stats['approved']}</b>\n"
        f"💵 فروش تاییدشده: <b>{stats['revenue']:,}</b>\n\n"
        f"🧩 کل پنل‌ها: <b>{stats['admins']}</b> · فعال: <b>{stats['active_admins']}</b>\n"
        f"📦 کل پلن‌ها: <b>{stats['plans']}</b> · فعال: <b>{stats['active_plans']}</b>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [await _button("گزارشات قدیمی", "sudo_menu_reports", fallback="📊")],
        [await _button("خانه", "back_to_main", icon_key="home", fallback="🏠")],
    ])
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


async def _render_tickets(message: Message, page: int = 0, status="open") -> None:
    await _ensure_schema()
    page = max(0, page)
    async with aiosqlite.connect(config.DATABASE_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute("SELECT COUNT(*) FROM support_tickets WHERE status=?", (status,)) as cur:
            total = int((await cur.fetchone())[0])
        pages = max(1, math.ceil(total / PAGE_SIZE))
        page = min(page, pages - 1)
        async with conn.execute(
            "SELECT id,user_id,subject,status,created_at FROM support_tickets WHERE status=? ORDER BY id DESC LIMIT ? OFFSET ?",
            (status, PAGE_SIZE, page * PAGE_SIZE),
        ) as cur:
            rows = await cur.fetchall()
    lines = ["🎧 <b>پشتیبانی و تیکت</b>", "", f"کل تیکت‌ها: <b>{total}</b>"]
    buttons = [[await _button("تیکت‌های باز", "cc:tickets:open:0"), await _button("بسته‌شده", "cc:tickets:closed:0")]]
    for row in rows:
        icon = "🟢" if row["status"] == "open" else "✅"
        lines.append(f"{icon} #{row['id']} · {escape(str(row['subject']))} · <code>{row['user_id']}</code>")
        buttons.append([await _button(f"تیکت #{row['id']}", f"cc:ticket:{row['id']}", fallback="🎫")])
    nav = []
    if page > 0:
        nav.append(await _button("قبلی", f"cc:tickets:{status}:{page-1}", fallback="⬅️"))
    if page + 1 < pages:
        nav.append(await _button("بعدی", f"cc:tickets:{status}:{page+1}", fallback="➡️"))
    if nav:
        buttons.append(nav)
    buttons.append([await _button("خانه", "back_to_main", icon_key="home", fallback="🏠")])
    await message.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@control_center_router.callback_query(F.data.startswith("cc:tickets:"))
async def control_center_tickets(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    await state.clear()
    try:
        page = int((callback.data or "").rsplit(":", 1)[-1])
    except ValueError:
        page = 0
    status = "closed" if ":closed:" in (callback.data or "") else "open"
    await _render_tickets(callback.message, page, status)
    await callback.answer()


@control_center_router.callback_query(F.data.startswith("cc:ticket:"))
async def control_center_ticket_detail(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    await state.clear()
    try:
        ticket_id = int((callback.data or "").rsplit(":", 1)[-1])
    except ValueError:
        await callback.answer("شناسه نامعتبر", show_alert=True)
        return
    await _ensure_schema()
    async with aiosqlite.connect(config.DATABASE_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute("SELECT * FROM support_tickets WHERE id=?", (ticket_id,)) as cur:
            row = await cur.fetchone()
    if not row:
        await callback.answer("تیکت پیدا نشد", show_alert=True)
        return
    text = (
        f"🎫 <b>تیکت #{row['id']}</b>\n\n"
        f"👤 <code>{row['user_id']}</code>\n"
        f"عنوان: {escape(str(row['subject']))}\n"
        f"وضعیت: <b>{escape(str(row['status']))}</b>\n\n"
        f"{escape(str(row['body'])[:1200])}"
    )
    if row["admin_reply"]:
        text += f"\n\n<b>پاسخ:</b>\n{escape(str(row['admin_reply']))}"
    rows = []
    if row["status"] == "open":
        rows.append([await _button("پاسخ", f"cc:ticketreply:{ticket_id}", fallback="✉️")])
        rows.append([await _button("بستن تیکت", f"cc:ticketclose:{ticket_id}", fallback="✅")])
    rows.append([await _button("بازگشت", "cc:tickets:0", icon_key="back", fallback="⬅️")])
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


@control_center_router.callback_query(F.data.startswith("cc:ticketreply:"))
async def control_center_ticket_reply_start(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    try:
        ticket_id = int((callback.data or "").rsplit(":", 1)[-1])
    except ValueError:
        await callback.answer("شناسه نامعتبر", show_alert=True)
        return
    await state.clear()
    await state.update_data(ticket_reply_id=ticket_id)
    await state.set_state(TicketReplyStates.waiting_for_reply)
    await callback.message.answer("پاسخ تیکت را ارسال کنید:")
    await callback.answer()


@control_center_router.message(TicketReplyStates.waiting_for_reply, F.text)
async def control_center_ticket_reply_value(message: Message, state: FSMContext):
    if not _is_sudo(message.from_user.id):
        return
    data = await state.get_data()
    ticket_id = data.get("ticket_reply_id")
    reply = (message.text or "").strip()
    if not ticket_id or not reply or len(reply) > 3500:
        await message.answer("پاسخ نامعتبر است.")
        return
    recipient = await support_service.reply_ticket(int(ticket_id), message.from_user.id, reply)
    if recipient is None:
        await state.clear()
        await message.answer("این تیکت بسته شده یا پیدا نشد.")
        return
    delivered = True
    try:
        await message.bot.send_message(recipient, config.MESSAGES["support_reply"].format(reply=escape(reply)))
    except Exception:
        delivered = False
    await state.clear()
    text = "پاسخ ارسال شد. تیکت باز می‌ماند." if delivered else "پاسخ ذخیره شد اما ارسال به کاربر ناموفق بود."
    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
        await _button("بازگشت به تیکت", f"cc:ticket:{ticket_id}")]]))


@control_center_router.callback_query(F.data.startswith("cc:ticketclose:"))
async def control_center_ticket_close(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    try:
        ticket_id = int((callback.data or "").rsplit(":", 1)[-1])
    except ValueError:
        await callback.answer("شناسه نامعتبر", show_alert=True)
        return
    await _ensure_schema()
    async with aiosqlite.connect(config.DATABASE_PATH) as conn:
        cur = await conn.execute(
            "UPDATE support_tickets SET status='closed', replied_by=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (callback.from_user.id, ticket_id),
        )
        await conn.commit()
        changed = cur.rowcount > 0
    await state.clear()
    if not changed:
        await callback.answer("تیکت پیدا نشد", show_alert=True)
        return
    await _render_tickets(callback.message, 0)
    await callback.answer("بسته شد")


@control_center_router.message(Command("ticket"))
async def ticket_create_command(message: Message, state: FSMContext):
    await _ensure_schema()
    await state.clear()
    await state.set_state(TicketCreateStates.waiting_for_subject)
    await message.answer(config.MESSAGES["support_subject"], reply_markup=await support_keyboard(message.from_user.id))


@control_center_router.message(TicketCreateStates.waiting_for_subject, F.text)
async def ticket_subject_value(message: Message, state: FSMContext):
    subject = (message.text or "").strip()
    if not subject or len(subject) > 120:
        await message.answer("عنوان باید بین ۱ تا ۱۲۰ کاراکتر باشد.")
        return
    await state.update_data(ticket_subject=subject)
    await state.set_state(TicketCreateStates.waiting_for_body)
    await message.answer(config.MESSAGES["support_body"], reply_markup=await support_keyboard(message.from_user.id))


@control_center_router.message(TicketCreateStates.waiting_for_body, F.text)
async def ticket_body_value(message: Message, state: FSMContext):
    body = (message.text or "").strip()
    data = await state.get_data()
    subject = data.get("ticket_subject")
    if not subject or not body or len(body) > 3500:
        await message.answer("متن تیکت نامعتبر است.")
        return
    await _ensure_schema()
    async with aiosqlite.connect(config.DATABASE_PATH) as conn:
        cur = await conn.execute(
            "INSERT INTO support_tickets(user_id,subject,body) VALUES(?,?,?)",
            (message.from_user.id, subject, body),
        )
        await conn.commit()
        ticket_id = int(cur.lastrowid)
    await state.clear()
    await message.answer(config.MESSAGES["support_submitted"], reply_markup=await support_keyboard(message.from_user.id))
    for sudo_id in config.SUDO_ADMINS:
        try:
            await message.bot.send_message(
                int(sudo_id),
                f"🎧 <b>تیکت جدید #{ticket_id}</b>\n👤 <code>{message.from_user.id}</code>\nعنوان: {escape(subject)}",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [await _button("مشاهده تیکت", f"cc:ticket:{ticket_id}", fallback="🎫")]
                ]),
            )
        except Exception:
            pass


async def support_keyboard(user_id):
    from database import db
    home = "back_to_admin_main" if await db.is_admin_authorized(user_id) else "public_back_main"
    return InlineKeyboardMarkup(inline_keyboard=[[await _button("بازگشت", home, fallback="⬅️")]])


@control_center_router.callback_query(F.data == "support:home")
async def support_home(callback, state):
    await state.clear()
    rows = [[await _button("ایجاد تیکت", "support:new", fallback="✉️")]]
    rows.extend((await support_keyboard(callback.from_user.id)).inline_keyboard)
    await callback.message.edit_text(config.MESSAGES["support_home"], reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


@control_center_router.callback_query(F.data == "support:new")
async def support_new(callback, state):
    await state.clear()
    await state.set_state(TicketCreateStates.waiting_for_subject)
    await callback.message.edit_text(config.MESSAGES["support_subject"], reply_markup=await support_keyboard(callback.from_user.id))
    await callback.answer()


@control_center_router.callback_query(F.data.startswith("cc:receipt:"))
async def order_receipt(callback):
    if await _deny(callback):
        return
    from database import db
    try:
        order = await db.get_order_by_id(int(callback.data.rsplit(':', 1)[-1]))
    except ValueError:
        order = None
    if not order or not order.get('receipt_file_id'):
        await callback.answer("رسید پیدا نشد.", show_alert=True)
        return
    await callback.message.answer_photo(order['receipt_file_id'], caption="رسید پرداخت")
    await callback.answer()
