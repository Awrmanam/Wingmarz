from __future__ import annotations

from datetime import datetime, timezone
from html import escape
import re
from typing import Any
from urllib.parse import urlparse

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    ChatMemberUpdated,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    KeyboardButtonRequestChat,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

import config
from authorization import is_staff
from database import db
from group_ops_service import TOPICS, group_ops_service
from kyc_service import kyc_service
from rebecca_api import RebeccaAPIError, rebecca_api
from trust_service import trust_service


group_ops_router = Router(name="group_ops")


OPS_CHAT_REQUEST_ID = 26018
ADMIN_PAGE_SIZE = 8


def _ops_home_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="👥 لیست ادمین‌ها", callback_data="gops:admins:0"),
            InlineKeyboardButton(text="🔎 جستجوی ادمین", callback_data="gops:search"),
        ],
        [
            InlineKeyboardButton(text="🔗 اتصال/تعویض گروه", callback_data="gops:connect"),
            InlineKeyboardButton(text="🧱 تکمیل تاپیک‌ها", callback_data="gops:repair"),
        ],
        [
            InlineKeyboardButton(text="📊 وضعیت Group Ops", callback_data="gops:status"),
            InlineKeyboardButton(text="🔄 بروزرسانی", callback_data="gops:home"),
        ],
        [InlineKeyboardButton(text="🔙 منوی اصلی", callback_data="back_to_main")],
    ])


def _ops_home_text() -> str:
    return (
        "🧭 <b>مرکز عملیات Wingmarz</b>\n\n"
        "مدیریت روزمره از همین دکمه‌ها انجام می‌شود؛ "
        "برای مدیریت Admin دیگر نیازی به نوشتن دستور ندارید."
    )


def _ops_group_picker_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[
            KeyboardButton(
                text="📌 انتخاب گروه مدیریت",
                request_chat=KeyboardButtonRequestChat(
                    request_id=OPS_CHAT_REQUEST_ID,
                    chat_is_channel=False,
                    chat_is_forum=True,
                    bot_is_member=True,
                ),
            )
        ]],
        resize_keyboard=True,
        one_time_keyboard=True,
        input_field_placeholder="گروه Forum مدیریت را انتخاب کنید",
    )


def _member_status_value(member) -> str:
    status = getattr(member, "status", "")
    return str(getattr(status, "value", status)).lower()


async def _send_ops_home(bot, chat_id: int, *, pin: bool = True):
    msg = await bot.send_message(
        int(chat_id),
        _ops_home_text(),
        reply_markup=_ops_home_keyboard(),
    )
    if pin:
        try:
            await bot.pin_chat_message(int(chat_id), msg.message_id, disable_notification=True)
        except Exception:
            pass
    return msg


async def _bootstrap_ops_group(bot, chat_id: int, actor_id: int) -> dict[str, int]:
    chat = await bot.get_chat(int(chat_id))
    if str(getattr(getattr(chat, "type", None), "value", getattr(chat, "type", ""))) != "supergroup":
        raise ValueError("گروه انتخابی باید Supergroup باشد")
    if not bool(getattr(chat, "is_forum", False)):
        raise ValueError("Topics/Forum برای این گروه فعال نیست")

    me = await bot.get_me()
    member = await bot.get_chat_member(int(chat_id), int(me.id))
    if _member_status_value(member) != "administrator":
        raise ValueError("بات باید Admin گروه باشد")
    if getattr(member, "can_manage_topics", None) is False:
        raise ValueError("مجوز Manage Topics برای بات فعال نیست")

    await group_ops_service.configure_group(int(chat_id), int(actor_id))
    topics = await group_ops_service.setup_topics(bot, int(chat_id))
    await group_ops_service.audit(
        actor_id=int(actor_id),
        action="group_ops.configure",
        subject_type="chat",
        subject_id=int(chat_id),
        new_value={"topics": topics},
    )
    await _send_ops_home(bot, int(chat_id))
    return topics


async def _render_ops_status(message: Message) -> None:
    cfg = await group_ops_service.get_config()
    topics = await group_ops_service.topics()
    if not cfg:
        text = (
            "⚪ <b>Group Ops هنوز متصل نیست.</b>\n\n"
            "روی «اتصال/تعویض گروه» بزنید و گروه Forum را انتخاب کنید."
        )
    else:
        text = (
            "📊 <b>وضعیت Group Ops</b>\n\n"
            f"Chat ID: <code>{int(cfg['chat_id'])}</code>\n"
            f"وضعیت: <b>{'فعال' if int(cfg.get('enabled') or 0) else 'خاموش'}</b>\n"
            f"Topics: <b>{len(topics)}/{len(TOPICS)}</b>"
        )
    await message.edit_text(text, reply_markup=_ops_home_keyboard())


async def _render_admin_list(message: Message, page: int = 0) -> None:
    admins = await db.get_all_admins()
    admins = sorted(
        admins,
        key=lambda a: (
            0 if bool(getattr(a, "is_active", False)) else 1,
            -(int(getattr(a, "id", 0) or 0)),
        ),
    )
    total = len(admins)
    pages = max(1, (total + ADMIN_PAGE_SIZE - 1) // ADMIN_PAGE_SIZE)
    page = max(0, min(int(page), pages - 1))
    start = page * ADMIN_PAGE_SIZE
    rows = []
    for admin in admins[start:start + ADMIN_PAGE_SIZE]:
        active = "🟢" if bool(getattr(admin, "is_active", False)) else "⚪"
        label = str(
            getattr(admin, "admin_name", None)
            or getattr(admin, "marzban_username", None)
            or f"Admin {admin.id}"
        )
        rows.append([InlineKeyboardButton(
            text=f"{active} {label[:28]} · {int(admin.user_id)}",
            callback_data=f"gops:admin:{int(admin.id)}",
        )])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️ قبلی", callback_data=f"gops:admins:{page-1}"))
    nav.append(InlineKeyboardButton(text=f"{page+1}/{pages}", callback_data="gops:noop"))
    if page + 1 < pages:
        nav.append(InlineKeyboardButton(text="بعدی ➡️", callback_data=f"gops:admins:{page+1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="🔎 جستجوی دقیق", callback_data="gops:search")])
    rows.append([InlineKeyboardButton(text="🧭 مرکز عملیات", callback_data="gops:home")])

    text = (
        "👥 <b>لیست Adminها</b>\n\n"
        f"تعداد کل: <b>{total}</b>\n"
        "برای باز کردن مدیریت کامل، روی Admin بزنید."
    )
    await message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


class AdminOpsStates(StatesGroup):
    search = State()
    subdomain = State()
    support_url = State()
    profile_title = State()
    data_limit_gb = State()
    expire_days = State()
    users_limit = State()
    telegram_id = State()


def _staff(user_id: int) -> bool:
    return is_staff(int(user_id))


async def _deny_event(event) -> bool:
    user = getattr(event, "from_user", None)
    if user and _staff(user.id):
        return False
    if isinstance(event, CallbackQuery):
        await event.answer("غیرمجاز", show_alert=True)
    return True


def _format_bytes(value: int | None) -> str:
    if not isinstance(value, int) or value <= 0:
        return "نامحدود"
    f = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if f < 1024 or unit == "TB":
            return f"{f:.2f} {unit}"
        f /= 1024
    return str(value)


def _support_value(raw: str) -> str:
    value = str(raw or "").strip()
    if value in {"-", "none", "null", "حذف"}:
        return ""
    if value.startswith("@"):
        username = value[1:].strip()
        if not re.fullmatch(r"[A-Za-z0-9_]{5,32}", username):
            raise ValueError("Telegram username نامعتبر است")
        return f"https://t.me/{username}"
    parsed = urlparse(value)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return value
    raise ValueError("لینک پشتیبانی باید @username یا URL کامل http(s) باشد")


def _merge_subscription_settings(raw: Any, key: str, value: str) -> dict[str, Any]:
    settings = dict(raw) if isinstance(raw, dict) else {}
    if value:
        settings[str(key)] = value
    else:
        settings.pop(str(key), None)
    return settings


def _domain_value(raw: str) -> str | None:
    value = str(raw or "").strip().lower().rstrip(".")
    if value in {"-", "none", "null", "حذف"}:
        return None
    if "://" in value or "/" in value or ":" in value:
        raise ValueError("فقط دامنه را وارد کنید؛ بدون https و path")
    if len(value) > 253 or not re.fullmatch(
        r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?",
        value,
    ):
        raise ValueError("دامنه معتبر نیست")
    return value


async def _trust_text(user_id: int) -> tuple[str, bool, bool]:
    profile = await kyc_service.get_profile(user_id)
    kyc_status = str((profile or {}).get("status") or "not_submitted")
    labels = {
        "approved": "🟢 تأیید شده",
        "pending": "🟡 در انتظار",
        "rejected": "🔴 رد شده",
        "not_submitted": "⚪ ثبت نشده",
    }
    trust = await trust_service.get(user_id)
    manual = bool(int(trust.get("manual_trusted") or 0))
    auto = bool(int(trust.get("auto_approve") or 0))
    return f"{labels.get(kyc_status, '⚪ نامشخص')} / {'🤝 Trusted' if manual else 'عادی'}", manual, auto


async def _remote_admin(admin):
    if config.PANEL_PROVIDER != "rebecca" or not admin.marzban_username:
        return None
    try:
        return await rebecca_api.find_admin(str(admin.marzban_username))
    except RebeccaAPIError:
        return None


async def _render_admin_card(message: Message, admin_id: int) -> bool:
    admin = await db.get_admin_by_id(int(admin_id))
    if not admin:
        await message.edit_text("❌ ادمین پیدا نشد.")
        return False

    remote = await _remote_admin(admin)
    trust_text, manual_trust, auto_approve = await _trust_text(int(admin.user_id))
    kyc_required = await kyc_service.is_required(int(admin.id))
    usage = None
    if remote and admin.marzban_username:
        try:
            usage = await rebecca_api.get_admin_usage(str(admin.marzban_username))
        except RebeccaAPIError:
            usage = None

    status = str((remote or {}).get("status") or ("active" if admin.is_active else "disabled"))
    data_limit = (remote or {}).get("data_limit")
    expire = (remote or {}).get("expire")
    users_limit = (remote or {}).get("users_limit")
    remaining = "نامحدود"
    if isinstance(expire, int) and expire > 0:
        seconds = expire - int(datetime.now(timezone.utc).timestamp())
        if seconds <= 0:
            remaining = "منقضی"
        elif seconds < 86400:
            remaining = f"{seconds // 3600} ساعت"
        else:
            remaining = f"{seconds // 86400} روز"

    text = (
        "👥 <b>مرکز مدیریت Admin</b>\n\n"
        f"🏷 نام: <b>{escape(str(admin.admin_name or admin.marzban_username or admin.id))}</b>\n"
        f"🔐 Rebecca: <code>{escape(str(admin.marzban_username or '-'))}</code>\n"
        f"🆔 Telegram: <code>{int(admin.user_id)}</code>\n"
        f"📱 Username: {('@' + escape(admin.username)) if admin.username else '—'}\n"
        f"🟢 وضعیت: <b>{escape(status)}</b>\n\n"
        f"📊 مصرف: <b>{_format_bytes(usage)}</b> / {_format_bytes(data_limit)}\n"
        f"⏳ اعتبار: <b>{escape(remaining)}</b>\n"
        f"👤 User Limit: <b>{users_limit if isinstance(users_limit, int) and users_limit > 0 else 'نامحدود'}</b>\n\n"
        f"🪪 KYC/Trust: <b>{trust_text}</b>\n"
        f"🔐 الزام KYC پنل: <b>{'فعال' if kyc_required else 'معاف'}</b>\n"
        f"🤖 Auto Approve: <b>{'روشن' if auto_approve else 'خاموش'}</b>"
    )

    trust_button = "❌ لغو Trust دستی" if manual_trust else "🤝 Trust بدون KYC"
    auto_button = "🤖 خاموش کردن Auto Approve" if auto_approve else "🤖 روشن کردن Auto Approve"
    status_button = "⛔ غیرفعال‌سازی" if status == "active" else "✅ فعال‌سازی"
    status_cb = f"manage_action_deactivate_{admin.id}" if status == "active" else f"manage_action_activate_{admin.id}"

    rows = [
        [
            InlineKeyboardButton(text="🌐 Subscription", callback_data=f"gops:sub:{admin.id}"),
            InlineKeyboardButton(text="📊 محدودیت‌ها", callback_data=f"gops:limits:{admin.id}"),
        ],
        [
            InlineKeyboardButton(text=trust_button, callback_data=f"gops:trust:{admin.id}"),
            InlineKeyboardButton(text=auto_button, callback_data=f"gops:auto:{admin.id}"),
        ],
        [
            InlineKeyboardButton(
                text="🔓 معاف از KYC" if kyc_required else "🔒 الزام KYC",
                callback_data=f"kycadmin:toggle:{admin.id}",
            ),
            InlineKeyboardButton(text=status_button, callback_data=status_cb),
        ],
        [
            InlineKeyboardButton(text="♻️ ریست مصرف", callback_data=f"gops:resetusage:{admin.id}"),
            InlineKeyboardButton(text="🔄 بروزرسانی", callback_data=f"gops:admin:{admin.id}"),
        ],
        [
            InlineKeyboardButton(text="👥 لیست Adminها", callback_data="gops:admins:0"),
            InlineKeyboardButton(text="🔎 جستجو", callback_data="gops:search"),
        ],
        [InlineKeyboardButton(text="🧭 مرکز عملیات", callback_data="gops:home")],
    ]
    await message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    return True


async def _render_subscription(message: Message, admin_id: int) -> None:
    admin = await db.get_admin_by_id(admin_id)
    if not admin or not admin.marzban_username:
        await message.edit_text("❌ ادمین پیدا نشد.")
        return
    try:
        settings = await rebecca_api.get_admin_subscription_settings(str(admin.marzban_username))
    except RebeccaAPIError as exc:
        await message.edit_text(f"❌ دریافت تنظیمات Rebecca ناموفق بود: {escape(str(exc))}")
        return
    overrides = settings.get("subscription_settings")
    overrides = overrides if isinstance(overrides, dict) else {}
    text = (
        "🌐 <b>Subscription Admin</b>\n\n"
        f"👤 <code>{escape(str(admin.marzban_username))}</code>\n"
        f"🌍 Subdomain: <code>{escape(str(settings.get('subscription_domain') or 'پیش‌فرض'))}</code>\n"
        f"🎧 Support: <code>{escape(str(overrides.get('subscription_support_url') or 'پیش‌فرض'))}</code>\n"
        f"📛 Profile title: <code>{escape(str(overrides.get('subscription_profile_title') or 'پیش‌فرض'))}</code>\n\n"
        "تغییرات مستقیماً روی admin-specific subscription settings در Rebecca اعمال می‌شوند."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌍 تنظیم Subdomain", callback_data=f"gops:setdomain:{admin.id}")],
        [InlineKeyboardButton(text="🎧 تنظیم Support", callback_data=f"gops:setsupport:{admin.id}")],
        [InlineKeyboardButton(text="📛 عنوان Subscription", callback_data=f"gops:settitle:{admin.id}")],
        [InlineKeyboardButton(text="🔙 Admin", callback_data=f"gops:admin:{admin.id}")],
    ])
    await message.edit_text(text, reply_markup=kb)


async def _render_limits(message: Message, admin_id: int) -> None:
    admin = await db.get_admin_by_id(admin_id)
    if not admin:
        await message.edit_text("❌ ادمین پیدا نشد.")
        return
    remote = await _remote_admin(admin)
    if not remote:
        await message.edit_text(
            "⚠️ اطلاعات زنده Rebecca دریافت نشد.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🔙 Admin", callback_data=f"gops:admin:{admin_id}")
            ]]),
        )
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📦 سقف حجم", callback_data=f"gops:setdata:{admin_id}")],
        [InlineKeyboardButton(text="⏳ زمان اعتبار", callback_data=f"gops:setexpire:{admin_id}")],
        [InlineKeyboardButton(text="👥 User Limit", callback_data=f"gops:setusers:{admin_id}")],
        [InlineKeyboardButton(text="🆔 Telegram ID", callback_data=f"gops:settelegram:{admin_id}")],
        [InlineKeyboardButton(text="🔙 Admin", callback_data=f"gops:admin:{admin_id}")],
    ])
    await message.edit_text(
        "📊 <b>محدودیت‌های Rebecca</b>\n\n"
        f"📦 حجم: <b>{_format_bytes(remote.get('data_limit'))}</b>\n"
        f"👥 User Limit: <b>{remote.get('users_limit') or 'نامحدود'}</b>\n"
        f"⏳ Expire timestamp: <code>{remote.get('expire') or 'نامحدود'}</code>\n"
        f"🆔 Telegram ID: <code>{remote.get('telegram_id') or '-'}</code>",
        reply_markup=kb,
    )


async def _find_admins(query: str):
    query = str(query or "").strip()
    if not query:
        return []
    admins = await db.get_all_admins()
    if query.isdigit():
        numeric = int(query)
        exact = [a for a in admins if a.id == numeric]
        by_user = [a for a in admins if int(a.user_id) == numeric]
        seen = set()
        result = []
        for item in exact + by_user:
            if item.id not in seen:
                seen.add(item.id)
                result.append(item)
        return result
    cleaned = query.lstrip("@").lower()
    return [
        a for a in admins
        if cleaned in {
            str(a.marzban_username or "").lower(),
            str(a.username or "").lstrip("@").lower(),
        }
        or cleaned in str(a.admin_name or "").lower()
    ]


async def _render_search_results(message: Message, query: str) -> None:
    admins = await _find_admins(query)
    if not admins:
        await message.answer("❌ ادمینی با این مشخصات پیدا نشد.")
        return
    if len(admins) == 1:
        # Message may be a normal message, not editable.
        fake = await message.answer("در حال بارگذاری...")
        await _render_admin_card(fake, int(admins[0].id))
        return
    rows = []
    for admin in admins[:30]:
        label = str(admin.admin_name or admin.marzban_username or admin.user_id)
        rows.append([InlineKeyboardButton(
            text=f"{label[:35]} · {admin.user_id}",
            callback_data=f"gops:admin:{admin.id}",
        )])
    rows.append([InlineKeyboardButton(text="🧭 مرکز عملیات", callback_data="gops:home")])
    await message.answer(
        f"🔎 {len(admins)} نتیجه پیدا شد:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@group_ops_router.message(Command("setup_group"))
async def setup_group(message: Message):
    """Backward-compatible bootstrap; button flow is the primary UX."""
    if await _deny_event(message):
        return
    try:
        topics = await _bootstrap_ops_group(message.bot, message.chat.id, message.from_user.id)
    except Exception as exc:
        await message.answer(
            "❌ راه‌اندازی گروه ناموفق بود.\n"
            f"<code>{escape(str(exc))}</code>"
        )
        return
    await message.answer(
        f"✅ Group Ops آماده شد؛ <b>{len(topics)}</b> تاپیک ثبت شد.",
        reply_markup=_ops_home_keyboard(),
    )


@group_ops_router.callback_query(F.data == "gops:home")
async def ops_home(callback: CallbackQuery, state: FSMContext):
    if await _deny_event(callback):
        return
    await state.clear()
    await callback.message.edit_text(_ops_home_text(), reply_markup=_ops_home_keyboard())
    await callback.answer()


@group_ops_router.callback_query(F.data == "gops:connect")
async def ops_connect(callback: CallbackQuery, state: FSMContext):
    if await _deny_event(callback):
        return
    await state.clear()
    await callback.message.answer(
        "🔗 <b>اتصال گروه مدیریت</b>\n\n"
        "دکمه زیر را بزنید و Supergroupای که Topics آن روشن است انتخاب کنید. "
        "بات باید از قبل داخل همان گروه Admin و دارای Manage Topics باشد.",
        reply_markup=_ops_group_picker_keyboard(),
    )
    await callback.answer()


@group_ops_router.message(F.chat_shared.request_id == OPS_CHAT_REQUEST_ID)
async def ops_chat_shared(message: Message):
    if await _deny_event(message):
        return
    shared = getattr(message, "chat_shared", None)
    if not shared or int(getattr(shared, "request_id", -1)) != OPS_CHAT_REQUEST_ID:
        return
    try:
        topics = await _bootstrap_ops_group(
            message.bot,
            int(shared.chat_id),
            message.from_user.id,
        )
    except Exception as exc:
        await message.answer(
            "❌ اتصال گروه انجام نشد.\n"
            f"<code>{escape(str(exc))}</code>",
            reply_markup=ReplyKeyboardRemove(),
        )
        return
    await message.answer(
        f"✅ گروه مدیریت متصل شد و <b>{len(topics)}</b> تاپیک آماده است.",
        reply_markup=ReplyKeyboardRemove(),
    )
    await message.answer(_ops_home_text(), reply_markup=_ops_home_keyboard())


@group_ops_router.my_chat_member()
async def ops_auto_bootstrap(event: ChatMemberUpdated):
    """Auto-bootstrap when a sudo user promotes the bot in a forum group."""
    actor = getattr(event, "from_user", None)
    chat = getattr(event, "chat", None)
    if not actor or not _staff(actor.id) or not chat:
        return
    chat_type = str(getattr(getattr(chat, "type", None), "value", getattr(chat, "type", "")))
    if chat_type != "supergroup" or not bool(getattr(chat, "is_forum", False)):
        return
    new_member = getattr(event, "new_chat_member", None)
    if _member_status_value(new_member) != "administrator":
        return
    try:
        await _bootstrap_ops_group(event.bot, chat.id, actor.id)
    except Exception:
        return


@group_ops_router.callback_query(F.data == "gops:status")
async def ops_status_callback(callback: CallbackQuery):
    if await _deny_event(callback):
        return
    await _render_ops_status(callback.message)
    await callback.answer()


@group_ops_router.callback_query(F.data == "gops:repair")
async def ops_repair(callback: CallbackQuery):
    if await _deny_event(callback):
        return
    cfg = await group_ops_service.get_config()
    if not cfg:
        await callback.answer("اول گروه را با دکمه اتصال انتخاب کنید.", show_alert=True)
        return
    try:
        topics = await group_ops_service.setup_topics(callback.bot, int(cfg["chat_id"]))
        await _send_ops_home(callback.bot, int(cfg["chat_id"]), pin=False)
    except Exception as exc:
        await callback.answer(f"خطا: {str(exc)[:160]}", show_alert=True)
        return
    await callback.answer(f"تاپیک‌ها آماده‌اند: {len(topics)}/{len(TOPICS)}", show_alert=True)
    await _render_ops_status(callback.message)


@group_ops_router.callback_query(F.data.startswith("gops:admins:"))
async def ops_admin_list(callback: CallbackQuery, state: FSMContext):
    if await _deny_event(callback):
        return
    await state.clear()
    try:
        page = int((callback.data or "").rsplit(":", 1)[-1])
    except ValueError:
        page = 0
    await _render_admin_list(callback.message, page)
    await callback.answer()


@group_ops_router.callback_query(F.data == "gops:noop")
async def ops_noop(callback: CallbackQuery):
    if await _deny_event(callback):
        return
    await callback.answer()


@group_ops_router.message(Command("group_status"))
async def group_status(message: Message):
    if await _deny_event(message):
        return
    cfg = await group_ops_service.get_config()
    topics = await group_ops_service.topics()
    if not cfg:
        await message.answer("گروه مدیریت هنوز تنظیم نشده. از دکمه «🧭 مرکز عملیات» → «اتصال/تعویض گروه» استفاده کنید.")
        return
    await message.answer(
        "🧭 <b>وضعیت Group Ops</b>\n\n"
        f"Chat ID: <code>{cfg['chat_id']}</code>\n"
        f"وضعیت: {'فعال' if cfg.get('enabled') else 'خاموش'}\n"
        f"Topics: <b>{len(topics)}/{len(TOPICS)}</b>"
    )


@group_ops_router.message(Command("admin"))
async def admin_command(message: Message):
    if await _deny_event(message):
        return
    query = (message.text or "").split(maxsplit=1)
    if len(query) < 2:
        await message.answer(
            "بعد از دستور، ID عددی / @username / Rebecca username را بفرستید.\n"
            "مثال: <code>/admin 7072365858</code>"
        )
        return
    await _render_search_results(message, query[1])


@group_ops_router.callback_query(F.data == "gops:search")
async def admin_search_start(callback: CallbackQuery, state: FSMContext):
    if await _deny_event(callback):
        return
    await state.clear()
    await state.set_state(AdminOpsStates.search)
    await callback.message.answer("🔎 ID عددی، @username یا Rebecca username ادمین را ارسال کنید.")
    await callback.answer()


@group_ops_router.message(AdminOpsStates.search, F.text)
async def admin_search_value(message: Message, state: FSMContext):
    if await _deny_event(message):
        return
    await state.clear()
    await _render_search_results(message, message.text or "")


@group_ops_router.callback_query(F.data.startswith("gops:admin:"))
async def admin_card(callback: CallbackQuery, state: FSMContext):
    if await _deny_event(callback):
        return
    await state.clear()
    admin_id = int((callback.data or "").rsplit(":", 1)[-1])
    await _render_admin_card(callback.message, admin_id)
    await callback.answer()


@group_ops_router.callback_query(F.data.startswith("gops:sub:"))
async def admin_subscription(callback: CallbackQuery, state: FSMContext):
    if await _deny_event(callback):
        return
    await state.clear()
    admin_id = int((callback.data or "").rsplit(":", 1)[-1])
    await _render_subscription(callback.message, admin_id)
    await callback.answer()


@group_ops_router.callback_query(F.data.startswith("gops:limits:"))
async def admin_limits(callback: CallbackQuery, state: FSMContext):
    if await _deny_event(callback):
        return
    await state.clear()
    admin_id = int((callback.data or "").rsplit(":", 1)[-1])
    await _render_limits(callback.message, admin_id)
    await callback.answer()


async def _set_state_for_admin(callback: CallbackQuery, state: FSMContext, target_state, prompt: str) -> None:
    admin_id = int((callback.data or "").rsplit(":", 1)[-1])
    await state.clear()
    await state.update_data(gops_admin_id=admin_id)
    await state.set_state(target_state)
    await callback.message.answer(prompt)
    await callback.answer()


@group_ops_router.callback_query(F.data.startswith("gops:setdomain:"))
async def set_domain_start(callback: CallbackQuery, state: FSMContext):
    if await _deny_event(callback):
        return
    await _set_state_for_admin(
        callback, state, AdminOpsStates.subdomain,
        "🌍 Subdomain را بدون https ارسال کنید. مثال: <code>sub.example.com</code>\nبرای حذف override: <code>-</code>",
    )


@group_ops_router.message(AdminOpsStates.subdomain, F.text)
async def set_domain_value(message: Message, state: FSMContext):
    if await _deny_event(message):
        return
    data = await state.get_data()
    admin_id = int(data.get("gops_admin_id") or 0)
    try:
        domain = _domain_value(message.text or "")
        admin = await db.get_admin_by_id(admin_id)
        if not admin or not admin.marzban_username:
            raise ValueError("ادمین پیدا نشد")
        current = await rebecca_api.get_admin_subscription_settings(str(admin.marzban_username))
        remote_id = int(current.get("id") or 0)
        before = current.get("subscription_domain")
        updated = await rebecca_api.update_admin_subscription_settings(
            remote_id, {"subscription_domain": domain}
        )
        await group_ops_service.audit(
            actor_id=message.from_user.id, action="rebecca.subscription_domain",
            subject_type="admin", subject_id=admin_id,
            old_value=before, new_value=updated.get("subscription_domain"),
        )
        await group_ops_service.send_text(
            message.bot, "admins",
            f"🌍 Subdomain ادمین <code>{escape(str(admin.marzban_username))}</code> تغییر کرد.\n"
            f"مقدار جدید: <code>{escape(str(updated.get('subscription_domain') or 'default'))}</code>"
        )
    except (ValueError, RebeccaAPIError) as exc:
        await message.answer(f"❌ {escape(str(exc))}")
        return
    await state.clear()
    await message.answer("✅ Subdomain در Rebecca ذخیره شد. توجه: DNS/SSL باید جداگانه روی دامنه آماده باشد.")


async def _update_subscription_override(message: Message, state: FSMContext, key: str, value: str) -> None:
    data = await state.get_data()
    admin_id = int(data.get("gops_admin_id") or 0)
    admin = await db.get_admin_by_id(admin_id)
    if not admin or not admin.marzban_username:
        raise ValueError("ادمین پیدا نشد")
    current = await rebecca_api.get_admin_subscription_settings(str(admin.marzban_username))
    remote_id = int(current.get("id") or 0)
    existing_settings = current.get("subscription_settings")
    existing_settings = dict(existing_settings) if isinstance(existing_settings, dict) else {}
    before = existing_settings.get(key)
    settings = _merge_subscription_settings(existing_settings, key, value)
    updated = await rebecca_api.update_admin_subscription_settings(
        remote_id, {"subscription_settings": settings}
    )
    await group_ops_service.audit(
        actor_id=message.from_user.id,
        action=f"rebecca.subscription_settings.{key}",
        subject_type="admin",
        subject_id=admin_id,
        old_value=before,
        new_value=(updated.get("subscription_settings") or {}).get(key),
    )
    await group_ops_service.send_text(
        message.bot,
        "admins",
        f"⚙️ تنظیم <code>{escape(key)}</code> برای <code>{escape(str(admin.marzban_username))}</code> تغییر کرد."
    )


@group_ops_router.callback_query(F.data.startswith("gops:setsupport:"))
async def set_support_start(callback: CallbackQuery, state: FSMContext):
    if await _deny_event(callback):
        return
    await _set_state_for_admin(
        callback, state, AdminOpsStates.support_url,
        "🎧 لینک پشتیبانی را ارسال کنید.\nمثال: <code>@arman_support</code> یا <code>https://t.me/arman_support</code>\nبرای حذف override: <code>-</code>",
    )


@group_ops_router.message(AdminOpsStates.support_url, F.text)
async def set_support_value(message: Message, state: FSMContext):
    if await _deny_event(message):
        return
    try:
        value = _support_value(message.text or "")
        await _update_subscription_override(message, state, "subscription_support_url", value)
    except (ValueError, RebeccaAPIError) as exc:
        await message.answer(f"❌ {escape(str(exc))}")
        return
    await state.clear()
    await message.answer("✅ لینک پشتیبانی Subscription ذخیره شد.")


@group_ops_router.callback_query(F.data.startswith("gops:settitle:"))
async def set_title_start(callback: CallbackQuery, state: FSMContext):
    if await _deny_event(callback):
        return
    await _set_state_for_admin(
        callback, state, AdminOpsStates.profile_title,
        "📛 عنوان Subscription را ارسال کنید. برای برگشت به مقدار پیش‌فرض <code>-</code> بفرستید.",
    )


@group_ops_router.message(AdminOpsStates.profile_title, F.text)
async def set_title_value(message: Message, state: FSMContext):
    if await _deny_event(message):
        return
    value = str(message.text or "").strip()
    if value in {"-", "none", "null", "حذف"}:
        value = ""
    if len(value) > 120:
        await message.answer("❌ عنوان بیش از حد طولانی است.")
        return
    try:
        await _update_subscription_override(message, state, "subscription_profile_title", value)
    except (ValueError, RebeccaAPIError) as exc:
        await message.answer(f"❌ {escape(str(exc))}")
        return
    await state.clear()
    await message.answer("✅ عنوان Subscription ذخیره شد.")


@group_ops_router.callback_query(F.data.startswith("gops:setdata:"))
async def set_data_start(callback: CallbackQuery, state: FSMContext):
    if await _deny_event(callback):
        return
    await _set_state_for_admin(
        callback, state, AdminOpsStates.data_limit_gb,
        "📦 سقف حجم Admin را به GB ارسال کنید. <code>0</code> یعنی نامحدود.",
    )


@group_ops_router.message(AdminOpsStates.data_limit_gb, F.text)
async def set_data_value(message: Message, state: FSMContext):
    if await _deny_event(message):
        return
    data = await state.get_data()
    admin_id = int(data.get("gops_admin_id") or 0)
    try:
        gb = float((message.text or "").replace(",", ".").strip())
        if gb < 0 or gb > 1_000_000:
            raise ValueError
    except ValueError:
        await message.answer("❌ مقدار GB معتبر نیست.")
        return
    admin = await db.get_admin_by_id(admin_id)
    if not admin or not admin.marzban_username:
        await message.answer("❌ ادمین پیدا نشد.")
        return
    value = None if gb == 0 else int(gb * 1024**3)
    try:
        remote = await rebecca_api.find_admin(str(admin.marzban_username))
        before = (remote or {}).get("data_limit")
        await rebecca_api.update_admin_fields(str(admin.marzban_username), {"data_limit": value})
        await db.update_admin(admin_id, max_total_traffic=(value or 0))
        await group_ops_service.audit(
            actor_id=message.from_user.id, action="rebecca.data_limit",
            subject_type="admin", subject_id=admin_id, old_value=before, new_value=value,
        )
    except RebeccaAPIError as exc:
        await message.answer(f"❌ {escape(str(exc))}")
        return
    await state.clear()
    await message.answer("✅ سقف حجم Rebecca بروزرسانی شد.")


@group_ops_router.callback_query(F.data.startswith("gops:setexpire:"))
async def set_expire_start(callback: CallbackQuery, state: FSMContext):
    if await _deny_event(callback):
        return
    await _set_state_for_admin(
        callback, state, AdminOpsStates.expire_days,
        "⏳ تعداد روز اعتبار از الان را ارسال کنید. <code>0</code> یعنی نامحدود.",
    )


@group_ops_router.message(AdminOpsStates.expire_days, F.text)
async def set_expire_value(message: Message, state: FSMContext):
    if await _deny_event(message):
        return
    data = await state.get_data()
    admin_id = int(data.get("gops_admin_id") or 0)
    try:
        days = int((message.text or "").strip())
        if days < 0 or days > 36500:
            raise ValueError
    except ValueError:
        await message.answer("❌ تعداد روز معتبر نیست.")
        return
    admin = await db.get_admin_by_id(admin_id)
    if not admin or not admin.marzban_username:
        await message.answer("❌ ادمین پیدا نشد.")
        return
    expire = None if days == 0 else int(datetime.now(timezone.utc).timestamp()) + days * 86400
    try:
        remote = await rebecca_api.find_admin(str(admin.marzban_username))
        before = (remote or {}).get("expire")
        await rebecca_api.update_admin_fields(str(admin.marzban_username), {"expire": expire})
        if days > 0:
            await db.update_admin(admin_id, validity_days=max(1, days), max_total_time=days * 86400)
        else:
            await db.update_admin(admin_id, validity_days=36500, max_total_time=0)
        await group_ops_service.audit(
            actor_id=message.from_user.id, action="rebecca.expire",
            subject_type="admin", subject_id=admin_id, old_value=before, new_value=expire,
        )
    except RebeccaAPIError as exc:
        await message.answer(f"❌ {escape(str(exc))}")
        return
    await state.clear()
    await message.answer("✅ اعتبار Rebecca بروزرسانی شد.")


@group_ops_router.callback_query(F.data.startswith("gops:setusers:"))
async def set_users_start(callback: CallbackQuery, state: FSMContext):
    if await _deny_event(callback):
        return
    await _set_state_for_admin(
        callback, state, AdminOpsStates.users_limit,
        "👥 سقف تعداد کاربر را ارسال کنید. <code>0</code> یعنی نامحدود.",
    )


@group_ops_router.message(AdminOpsStates.users_limit, F.text)
async def set_users_value(message: Message, state: FSMContext):
    if await _deny_event(message):
        return
    data = await state.get_data()
    admin_id = int(data.get("gops_admin_id") or 0)
    try:
        limit = int((message.text or "").strip())
        if limit < 0 or limit > 1_000_000:
            raise ValueError
    except ValueError:
        await message.answer("❌ User Limit معتبر نیست.")
        return
    admin = await db.get_admin_by_id(admin_id)
    if not admin or not admin.marzban_username:
        await message.answer("❌ ادمین پیدا نشد.")
        return
    value = None if limit == 0 else limit
    try:
        remote = await rebecca_api.find_admin(str(admin.marzban_username))
        before = (remote or {}).get("users_limit")
        await rebecca_api.update_admin_fields(str(admin.marzban_username), {"users_limit": value})
        await db.update_admin(admin_id, max_users=(value or 1_000_000))
        await group_ops_service.audit(
            actor_id=message.from_user.id, action="rebecca.users_limit",
            subject_type="admin", subject_id=admin_id, old_value=before, new_value=value,
        )
    except RebeccaAPIError as exc:
        await message.answer(f"❌ {escape(str(exc))}")
        return
    await state.clear()
    await message.answer("✅ User Limit بروزرسانی شد.")


@group_ops_router.callback_query(F.data.startswith("gops:settelegram:"))
async def set_telegram_start(callback: CallbackQuery, state: FSMContext):
    if await _deny_event(callback):
        return
    await _set_state_for_admin(
        callback, state, AdminOpsStates.telegram_id,
        "🆔 Telegram ID عددی مالک Admin را ارسال کنید.",
    )


@group_ops_router.message(AdminOpsStates.telegram_id, F.text)
async def set_telegram_value(message: Message, state: FSMContext):
    if await _deny_event(message):
        return
    data = await state.get_data()
    admin_id = int(data.get("gops_admin_id") or 0)
    raw = (message.text or "").strip()
    if not raw.isdigit() or int(raw) <= 0:
        await message.answer("❌ Telegram ID باید عدد مثبت باشد.")
        return
    new_user_id = int(raw)
    admin = await db.get_admin_by_id(admin_id)
    if not admin or not admin.marzban_username:
        await message.answer("❌ ادمین پیدا نشد.")
        return
    try:
        remote = await rebecca_api.find_admin(str(admin.marzban_username))
        before = (remote or {}).get("telegram_id")
        await rebecca_api.update_admin_fields(str(admin.marzban_username), {"telegram_id": new_user_id})
        await db.update_admin(admin_id, user_id=new_user_id)
        await group_ops_service.audit(
            actor_id=message.from_user.id, action="rebecca.telegram_id",
            subject_type="admin", subject_id=admin_id, old_value=before, new_value=new_user_id,
        )
    except RebeccaAPIError as exc:
        await message.answer(f"❌ {escape(str(exc))}")
        return
    await state.clear()
    await message.answer("✅ Telegram ID مالک بروزرسانی شد.")


@group_ops_router.callback_query(F.data.startswith("gops:trust:"))
async def toggle_manual_trust(callback: CallbackQuery):
    if await _deny_event(callback):
        return
    admin_id = int((callback.data or "").rsplit(":", 1)[-1])
    admin = await db.get_admin_by_id(admin_id)
    if not admin:
        await callback.answer("ادمین پیدا نشد.", show_alert=True)
        return
    before = await trust_service.is_manually_trusted(admin.user_id)
    await trust_service.set_manual_trusted(admin.user_id, not before, callback.from_user.id)
    await group_ops_service.audit(
        actor_id=callback.from_user.id, action="trust.manual",
        subject_type="user", subject_id=admin.user_id, old_value=before, new_value=not before,
    )
    await group_ops_service.send_text(
        callback.bot,
        "admins",
        f"🤝 Trust دستی کاربر <code>{admin.user_id}</code> {'فعال' if not before else 'لغو'} شد."
    )
    await _render_admin_card(callback.message, admin_id)
    await callback.answer("ذخیره شد")


@group_ops_router.callback_query(F.data.startswith("gops:auto:"))
async def toggle_auto_approve(callback: CallbackQuery):
    if await _deny_event(callback):
        return
    admin_id = int((callback.data or "").rsplit(":", 1)[-1])
    admin = await db.get_admin_by_id(admin_id)
    if not admin:
        await callback.answer("ادمین پیدا نشد.", show_alert=True)
        return
    before = await trust_service.auto_approve_enabled(admin.user_id)
    try:
        await trust_service.set_auto_approve(admin.user_id, not before)
    except ValueError:
        await callback.answer("اول KYC را تأیید یا کاربر را Manual Trust کنید.", show_alert=True)
        return
    await group_ops_service.audit(
        actor_id=callback.from_user.id, action="trust.auto_approve",
        subject_type="user", subject_id=admin.user_id, old_value=before, new_value=not before,
    )
    await _render_admin_card(callback.message, admin_id)
    if not before:
        await callback.answer("Auto Approve مجاز شد؛ صدور خودکار فقط بعد از Bank Match واقعی انجام می‌شود.", show_alert=True)
    else:
        await callback.answer("Auto Approve خاموش شد.")


@group_ops_router.callback_query(F.data.startswith("gops:resetusage:"))
async def reset_usage(callback: CallbackQuery):
    if await _deny_event(callback):
        return
    admin_id = int((callback.data or "").rsplit(":", 1)[-1])
    admin = await db.get_admin_by_id(admin_id)
    if not admin or not admin.marzban_username:
        await callback.answer("ادمین پیدا نشد.", show_alert=True)
        return
    try:
        before = await rebecca_api.get_admin_usage(str(admin.marzban_username))
        await rebecca_api.reset_admin_usage(str(admin.marzban_username))
        await group_ops_service.audit(
            actor_id=callback.from_user.id, action="rebecca.usage_reset",
            subject_type="admin", subject_id=admin_id, old_value=before, new_value=0,
        )
    except RebeccaAPIError as exc:
        await callback.answer(str(exc)[:150], show_alert=True)
        return
    await callback.answer("مصرف Rebecca ریست شد ✅", show_alert=True)
    await _render_admin_card(callback.message, admin_id)
