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
from style_engine import style_engine


control_center_router = Router(name="control_center")
PAGE_SIZE = 8


class TicketCreateStates(StatesGroup):
    waiting_for_subject = State()
    waiting_for_body = State()


class TicketReplyStates(StatesGroup):
    waiting_for_reply = State()


def _is_sudo(user_id: int) -> bool:
    return user_id in config.SUDO_ADMINS


async def _deny(callback: CallbackQuery) -> bool:
    if _is_sudo(callback.from_user.id):
        return False
    await callback.answer("غیرمجاز", show_alert=True)
    return True


async def _ensure_schema() -> None:
    async with aiosqlite.connect(config.DATABASE_PATH) as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS support_tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                subject TEXT NOT NULL,
                body TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                admin_reply TEXT,
                replied_by INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await conn.commit()


async def _button(text: str, callback_data: str, *, icon_key: str | None = None, fallback: str | None = None):
    return await style_engine.styled_button(
        text,
        icon_key=icon_key,
        fallback=fallback,
        callback_data=callback_data,
    )


async def build_control_center_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [
            await _button("فروش و تعرفه‌ها", "sudo_menu_sales", icon_key="sales", fallback="🛒"),
            await _button("مرکز پنل‌ها", "sudo_menu_panels", icon_key="panel", fallback="🧩"),
        ],
        [
            await _button("سفارش‌ها", "cc:orders:0", icon_key="orders", fallback="🧾"),
            await _button("دسته‌بندی پلن‌ها", "sales_manage", icon_key="plan", fallback="📦"),
        ],
        [
            await _button("کاربران", "cc:users:0", icon_key="users", fallback="👥"),
            await _button("مالی و پرداخت", "sudo_menu_sales", icon_key="finance", fallback="💵"),
        ],
        [
            await _button("تخفیف‌ها", "cc:discounts", icon_key="discount", fallback="🎟"),
            await _button("کانفیگ تست", "cc:test", icon_key="test", fallback="🧪"),
        ],
        [
            await _button("ادمین‌های ربات", "cc:botadmins", icon_key="admin", fallback="🧑‍💼"),
            await _button("آمار و گزارشات", "cc:stats", icon_key="stats", fallback="📊"),
        ],
        [
            await _button("اطلاع‌رسانی", "sudo_menu_broadcast", icon_key="broadcast", fallback="📢"),
            await _button("پشتیبانی و تیکت", "cc:tickets:0", icon_key="support", fallback="🎧"),
        ],
        [
            await _button("ایموجی و استایل", "style:menu", icon_key="style", fallback="🎨"),
            await _button("مدیریت متن‌ها", "cc:texts", icon_key="text", fallback="📝"),
        ],
        [
            await _button("دکمه‌ها و منوها", "cc:buttons", icon_key="buttons", fallback="🔘"),
            await _button("ابزارها و بکاپ", "sudo_menu_backup", icon_key="tools", fallback="🧰"),
        ],
        [await _button("تنظیمات", "sudo_menu_settings", icon_key="settings", fallback="⚙️")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _dashboard_text() -> str:
    async with aiosqlite.connect(config.DATABASE_PATH) as conn:
        queries = {
            "orders": "SELECT COUNT(*) FROM orders",
            "pending": "SELECT COUNT(*) FROM orders WHERE status='pending'",
            "admins": "SELECT COUNT(*) FROM admins WHERE is_active=1",
            "plans": "SELECT COUNT(*) FROM plans WHERE is_active=1",
        }
        values: dict[str, int] = {}
        for key, query in queries.items():
            try:
                async with conn.execute(query) as cur:
                    row = await cur.fetchone()
                    values[key] = int((row or [0])[0] or 0)
            except aiosqlite.OperationalError:
                values[key] = 0
    return (
        "🏠 <b>مرکز مدیریت ربات</b>\n\n"
        f"🧾 سفارش‌ها: <b>{values['orders']}</b>  |  در انتظار: <b>{values['pending']}</b>\n"
        f"🧩 پنل‌های فعال: <b>{values['admins']}</b>  |  پلن‌های فعال: <b>{values['plans']}</b>\n\n"
        "یک بخش را انتخاب کنید."
    )


async def _render_dashboard(message: Message) -> None:
    await message.edit_text(await _dashboard_text(), reply_markup=await build_control_center_keyboard())


async def _send_dashboard(message: Message) -> None:
    await message.answer(await _dashboard_text(), reply_markup=await build_control_center_keyboard())


@control_center_router.message(Command("dashboard"))
@control_center_router.message(Command("admin"))
async def control_center_command(message: Message, state: FSMContext):
    if not _is_sudo(message.from_user.id):
        return
    await state.clear()
    await _ensure_schema()
    await _send_dashboard(message)


@control_center_router.callback_query(F.data == "back_to_main")
async def control_center_back(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    await state.clear()
    await _ensure_schema()
    await _render_dashboard(callback.message)
    await callback.answer()


async def _order_count(conn: aiosqlite.Connection) -> int:
    try:
        async with conn.execute("SELECT COUNT(*) FROM orders") as cur:
            row = await cur.fetchone()
            return int((row or [0])[0] or 0)
    except aiosqlite.OperationalError:
        return 0


async def _render_orders(message: Message, page: int) -> None:
    page = max(0, page)
    async with aiosqlite.connect(config.DATABASE_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        total = await _order_count(conn)
        pages = max(1, math.ceil(total / PAGE_SIZE))
        page = min(page, pages - 1)
        try:
            async with conn.execute(
                """
                SELECT id, user_id, status, order_type, price_snapshot,
                       plan_name_snapshot, created_at
                FROM orders
                ORDER BY id DESC
                LIMIT ? OFFSET ?
                """,
                (PAGE_SIZE, page * PAGE_SIZE),
            ) as cur:
                rows = await cur.fetchall()
        except aiosqlite.OperationalError:
            rows = []

    lines = ["🧾 <b>سفارش‌ها</b>", "", f"کل: <b>{total}</b>"]
    buttons = []
    if not rows:
        lines.extend(["", "سفارشی ثبت نشده است."])
    for row in rows:
        status = escape(str(row["status"] or "-"))
        plan = escape(str(row["plan_name_snapshot"] or "پلن"))
        price = int(row["price_snapshot"] or 0)
        lines.append(f"#{row['id']} · {plan} · {status} · {price:,}")
        buttons.append([await _button(f"سفارش #{row['id']}", f"cc:order:{row['id']}", fallback="🧾")])

    nav = []
    if page > 0:
        nav.append(await _button("قبلی", f"cc:orders:{page-1}", fallback="⬅️"))
    if page + 1 < pages:
        nav.append(await _button("بعدی", f"cc:orders:{page+1}", fallback="➡️"))
    if nav:
        buttons.append(nav)
    buttons.append([await _button("خانه", "back_to_main", icon_key="home", fallback="🏠")])
    await message.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@control_center_router.callback_query(F.data.startswith("cc:orders:"))
async def control_center_orders(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    await state.clear()
    try:
        page = int((callback.data or "").rsplit(":", 1)[-1])
    except ValueError:
        page = 0
    await _render_orders(callback.message, page)
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
        f"💳 وضعیت: <b>{escape(str(row['status'] or '-'))}</b>\n"
        f"💵 مبلغ: <b>{int(row['price_snapshot'] or 0):,}</b>\n"
        f"🕒 ثبت: {escape(str(row['created_at'] or '-'))}\n"
        f"✅ تاییدکننده: {escape(str(row['approved_by'] or '-'))}\n"
        f"🧩 پنل صادرشده: {escape(str(row['issued_admin_id'] or '-'))}"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
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


@control_center_router.callback_query(F.data == "cc:test")
async def control_center_test(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    await state.clear()
    provider = config.PANEL_PROVIDER
    ok = False
    detail = ""
    try:
        if provider == "rebecca":
            from rebecca_api import rebecca_api
            result = await rebecca_api.health_check()
            ok = bool(result)
        else:
            from marzban_api import marzban_api
            ok = bool(await marzban_api.test_connection())
    except Exception as exc:
        detail = type(exc).__name__
    status = "✅ اتصال برقرار است" if ok else "❌ اتصال برقرار نیست"
    text = (
        "🧪 <b>تست اتصال پنل</b>\n\n"
        f"Provider: <code>{escape(provider)}</code>\n"
        f"وضعیت: <b>{status}</b>"
    )
    if detail:
        text += f"\nخطا: <code>{escape(detail)}</code>"
    text += "\n\nاین تست فقط خواندنی است و کاربر/کانفیگی ایجاد یا حذف نمی‌کند."
    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [await _button("خانه", "back_to_main", icon_key="home", fallback="🏠")]
        ]),
    )
    await callback.answer()


@control_center_router.callback_query(F.data == "cc:botadmins")
async def control_center_botadmins(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    await state.clear()
    ids = sorted(set(int(x) for x in config.SUDO_ADMINS))
    lines = ["🧑‍💼 <b>ادمین‌های ربات</b>", "", "SUDOهای فعال از تنظیمات امن سرور:"]
    lines.extend(f"• <code>{user_id}</code>" for user_id in ids)
    lines.extend(["", "برای جلوگیری از دور زدن سطح دسترسی، SUDO اصلی از env/config کنترل می‌شود."])
    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [await _button("مدیریت پنل‌ها", "sudo_menu_panels", fallback="🧩")],
            [await _button("خانه", "back_to_main", icon_key="home", fallback="🏠")],
        ]),
    )
    await callback.answer()


@control_center_router.callback_query(F.data == "cc:discounts")
async def control_center_discounts(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    await state.clear()
    await callback.message.edit_text(
        "🎟 <b>تخفیف‌ها</b>\n\n"
        "موتور تخفیف مستقل هنوز به checkout فعلی وصل نشده است؛ برای جلوگیری از نمایش کد تخفیفِ ظاهراً فعال ولی بی‌اثر، این بخش تا اتصال کامل به سفارش عمداً فقط وضعیت را نشان می‌دهد.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [await _button("فروش و تعرفه‌ها", "sudo_menu_sales", fallback="🛒")],
            [await _button("خانه", "back_to_main", icon_key="home", fallback="🏠")],
        ]),
    )
    await callback.answer()


@control_center_router.callback_query(F.data == "cc:texts")
async def control_center_texts(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    await state.clear()
    overrides = await style_engine.list_overrides()
    await callback.message.edit_text(
        "📝 <b>مدیریت متن‌ها</b>\n\n"
        f"Text Overrideهای فعال: <b>{len(overrides)}</b>\n"
        "متن نمایشی از هویت خام جدا نگه داشته می‌شود تا callback و lookup خراب نشود.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [await _button("باز کردن Text Overrides", "style:aliases", fallback="📝")],
            [await _button("خانه", "back_to_main", icon_key="home", fallback="🏠")],
        ]),
    )
    await callback.answer()


@control_center_router.callback_query(F.data == "cc:buttons")
async def control_center_buttons(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    await state.clear()
    await callback.message.edit_text(
        "🔘 <b>دکمه‌ها و منوها</b>\n\n"
        "ظاهر دکمه‌ها از Style Engine کنترل می‌شود و callback_data ثابت می‌ماند.\n"
        "برای تغییر ایموجی، fallback و ظاهر فعلی از بخش «ایموجی و استایل» استفاده کنید.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [await _button("ایموجی و استایل", "style:menu", icon_key="style", fallback="🎨")],
            [await _button("خانه", "back_to_main", icon_key="home", fallback="🏠")],
        ]),
    )
    await callback.answer()


async def _render_tickets(message: Message, page: int = 0) -> None:
    await _ensure_schema()
    page = max(0, page)
    async with aiosqlite.connect(config.DATABASE_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute("SELECT COUNT(*) FROM support_tickets") as cur:
            total = int((await cur.fetchone())[0])
        pages = max(1, math.ceil(total / PAGE_SIZE))
        page = min(page, pages - 1)
        async with conn.execute(
            "SELECT id,user_id,subject,status,created_at FROM support_tickets ORDER BY id DESC LIMIT ? OFFSET ?",
            (PAGE_SIZE, page * PAGE_SIZE),
        ) as cur:
            rows = await cur.fetchall()
    lines = ["🎧 <b>پشتیبانی و تیکت</b>", "", f"کل تیکت‌ها: <b>{total}</b>"]
    buttons = []
    for row in rows:
        icon = "🟢" if row["status"] == "open" else "✅"
        lines.append(f"{icon} #{row['id']} · {escape(str(row['subject']))} · <code>{row['user_id']}</code>")
        buttons.append([await _button(f"تیکت #{row['id']}", f"cc:ticket:{row['id']}", fallback="🎫")])
    nav = []
    if page > 0:
        nav.append(await _button("قبلی", f"cc:tickets:{page-1}", fallback="⬅️"))
    if page + 1 < pages:
        nav.append(await _button("بعدی", f"cc:tickets:{page+1}", fallback="➡️"))
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
    await _render_tickets(callback.message, page)
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
        f"{escape(str(row['body']))}"
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
    await _ensure_schema()
    async with aiosqlite.connect(config.DATABASE_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute("SELECT user_id,status FROM support_tickets WHERE id=?", (int(ticket_id),)) as cur:
            row = await cur.fetchone()
        if not row:
            await state.clear()
            await message.answer("تیکت پیدا نشد.")
            return
        await conn.execute(
            "UPDATE support_tickets SET admin_reply=?, replied_by=?, status='closed', updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (reply, message.from_user.id, int(ticket_id)),
        )
        await conn.commit()
    try:
        await message.bot.send_message(
            int(row["user_id"]),
            f"🎧 <b>پاسخ تیکت #{ticket_id}</b>\n\n{escape(reply)}",
        )
    except Exception:
        pass
    await state.clear()
    await message.answer(f"✅ پاسخ تیکت #{ticket_id} ذخیره و تیکت بسته شد.")


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
    await message.answer("🎧 عنوان کوتاه تیکت را ارسال کنید:")


@control_center_router.message(TicketCreateStates.waiting_for_subject, F.text)
async def ticket_subject_value(message: Message, state: FSMContext):
    subject = (message.text or "").strip()
    if not subject or len(subject) > 120:
        await message.answer("عنوان باید بین ۱ تا ۱۲۰ کاراکتر باشد.")
        return
    await state.update_data(ticket_subject=subject)
    await state.set_state(TicketCreateStates.waiting_for_body)
    await message.answer("متن درخواست را ارسال کنید:")


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
    await message.answer(f"✅ تیکت #{ticket_id} ثبت شد.")
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
