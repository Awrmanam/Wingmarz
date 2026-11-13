from __future__ import annotations

from datetime import datetime, timezone
from html import escape
from typing import Any

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

import config
from database import db
from rebecca_api import RebeccaAPIError, rebecca_api
from utils.notify import format_traffic_size


rebecca_admin_sync_router = Router(name="rebecca_admin_live_sync")


def _back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=config.BUTTONS["back"], callback_data="back_to_admin_main")
    ]])


def _as_non_negative_int(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 0 else default


def _status_breakdown(snapshot: dict[str, Any]) -> dict[str, int]:
    raw = snapshot.get("status_breakdown")
    if not isinstance(raw, dict):
        return {}
    return {str(key).lower(): _as_non_negative_int(value) for key, value in raw.items()}


def _active_total(snapshot: dict[str, Any]) -> int:
    value = snapshot.get("active_total")
    if value is not None:
        return _as_non_negative_int(value)
    return _status_breakdown(snapshot).get("active", 0)


def _online_total(snapshot: dict[str, Any]) -> int:
    value = snapshot.get("online_total")
    if value is not None:
        return _as_non_negative_int(value)
    users = snapshot.get("users")
    if not isinstance(users, list):
        return 0
    return sum(1 for user in users if isinstance(user, dict) and bool(user.get("is_online")))


def _status_count(snapshot: dict[str, Any], *names: str) -> int:
    breakdown = _status_breakdown(snapshot)
    return sum(breakdown.get(name.lower(), 0) for name in names)


async def _admin_users_snapshot(username: str, *, limit: int = 10, offset: int = 0) -> dict[str, Any]:
    """Read Rebecca's official admin-scoped users endpoint.

    The bearer token used by Wingmarz is a global admin token, so the explicit
    `admin` query parameter is required to keep every view scoped to the panel
    selected by the Telegram user.
    """
    requested = str(username or "").strip()
    if not requested:
        raise RebeccaAPIError("Rebecca admin username is required")
    data = await rebecca_api._request(
        "GET",
        "/api/users",
        params={"admin": requested, "limit": max(1, min(int(limit), 100)), "offset": max(0, int(offset)), "sort": "username"},
    )
    if not isinstance(data, dict):
        raise RebeccaAPIError("Rebecca returned an invalid users response")
    users = data.get("users")
    if users is None:
        users = []
    if not isinstance(users, list) or any(not isinstance(item, dict) for item in users):
        raise RebeccaAPIError("Rebecca returned an invalid users list")
    for user in users:
        remote_owner = user.get("admin")
        if remote_owner is not None and str(remote_owner) != requested:
            raise RebeccaAPIError("Rebecca returned users outside requested reseller scope")
    total = data.get("total")
    if total is not None and (isinstance(total, bool) or not isinstance(total, int) or total < 0):
        raise RebeccaAPIError("Rebecca returned an invalid users total")
    data = dict(data)
    data["users"] = users
    data["total"] = _as_non_negative_int(total, len(users))
    return data


async def _live_panel(admin: Any) -> tuple[dict[str, Any], dict[str, Any], int]:
    username = str(getattr(admin, "marzban_username", "") or "").strip()
    remote = await rebecca_api.find_admin(username)
    if remote is None:
        raise RebeccaAPIError("Rebecca admin was not found")
    snapshot = await _admin_users_snapshot(username)
    usage = await rebecca_api.get_admin_usage(username)
    return remote, snapshot, int(usage)


def _expires_text(raw_value: Any) -> tuple[str, str]:
    if isinstance(raw_value, bool) or not isinstance(raw_value, (float, int)) or raw_value <= 0:
        return "نامحدود", "—"
    expiry = datetime.fromtimestamp(float(raw_value), tz=timezone.utc)
    remaining = max(0, int(expiry.timestamp() - datetime.now(timezone.utc).timestamp()))
    if remaining == 0:
        label = "منقضی شده"
    elif remaining >= 86400:
        label = f"{remaining // 86400} روز و {(remaining % 86400) // 3600} ساعت"
    else:
        label = f"{remaining // 3600} ساعت و {(remaining % 3600) // 60} دقیقه"
    return label, expiry.strftime("%Y-%m-%d %H:%M UTC")


async def _render_info(callback: CallbackQuery, admin: Any) -> None:
    try:
        remote, snapshot, usage = await _live_panel(admin)
        status_raw = str(remote.get("status", "")).lower()
        status = "فعال" if status_raw == "active" else "غیرفعال" if status_raw == "disabled" else status_raw or "نامشخص"
        limit = remote.get("data_limit")
        users_limit = remote.get("users_limit")
        total = _as_non_negative_int(snapshot.get("total"))
        active = _active_total(snapshot)
        online = _online_total(snapshot)
        disabled = _status_count(snapshot, "disabled")
        expired = _status_count(snapshot, "expired")
        limited = _status_count(snapshot, "limited", "quota_full")

        users_limit_text = "نامحدود"
        if isinstance(users_limit, int) and not isinstance(users_limit, bool) and users_limit > 0:
            users_limit_text = f"{users_limit:,}"
        traffic_limit_text = "نامحدود"
        if isinstance(limit, int) and not isinstance(limit, bool) and limit > 0:
            traffic_limit_text = await format_traffic_size(limit)

        time_left, expiry_date = _expires_text(remote.get("expire"))
        remaining_data = (
            max(0, limit - usage)
            if isinstance(limit, int) and not isinstance(limit, bool) and limit > 0
            else None
        )
        percent_used = (100 * usage / limit) if remaining_data is not None else None
        lines = [
            "👤 <b>اطلاعات پنل</b>",
            "",
            f"🏷 نام: <b>{escape(str(admin.admin_name or admin.marzban_username or '-'))}</b>",
            f"🔐 نام کاربری: <code>{escape(str(admin.marzban_username or '-'))}</code>",
            f"🟢 وضعیت: <b>{escape(status)}</b>",
            "",
            "👥 <b>کاربران زنده Rebecca</b>",
            f"• کل: <b>{total}</b> / {users_limit_text}",
            f"• فعال: <b>{active}</b>",
            f"• آنلاین: <b>{online}</b>",
            f"• غیرفعال: <b>{disabled}</b>",
            f"• منقضی: <b>{expired}</b>",
        ]
        if limited:
            lines.append(f"• اتمام حجم/محدود: <b>{limited}</b>")
        lines.extend([
            "",
            "📊 <b>ترافیک زنده Rebecca</b>",
            f"• مصرف‌شده: <b>{escape(await format_traffic_size(usage))}</b>",
            f"• سقف: <b>{escape(traffic_limit_text)}</b>",
            f"• باقی‌مانده: <b>{escape(await format_traffic_size(remaining_data)) if remaining_data is not None else 'نامحدود'}</b>",
            f"• درصد مصرف: <b>{percent_used:.1f}%</b>" if percent_used is not None else "• درصد مصرف: بدون محدودیت",
            "",
            "⏳ <b>زمان اعتبار</b>",
            f"• باقی‌مانده: <b>{escape(time_left)}</b>",
            f"• پایان: <b>{escape(expiry_date)}</b>",
            "",
            f"🆔 شناسه مالک: <code>{int(admin.user_id)}</code>",
            "<i>آمار از Rebecca خوانده شده و به‌صورت محلی تخمین زده نشده است.</i>",
        ])
        await callback.message.edit_text("\n".join(lines), reply_markup=_back_keyboard())
    except Exception:
        await callback.message.edit_text(
            "⚠️ دریافت اطلاعات زنده Rebecca موقتاً ناموفق بود. دوباره تلاش کنید.",
            reply_markup=_back_keyboard(),
        )
    await callback.answer()


async def _render_report(callback: CallbackQuery, admin: Any) -> None:
    try:
        _remote, snapshot, usage = await _live_panel(admin)
        total = _as_non_negative_int(snapshot.get("total"))
        active = _active_total(snapshot)
        online = _online_total(snapshot)
        lines = [
            f"📈 <b>گزارش لحظه‌ای پنل: {escape(str(admin.admin_name or admin.marzban_username or '-'))}</b>",
            "",
            f"👥 تعداد کل کاربران: <b>{total}</b>",
            f"✅ کاربران فعال: <b>{active}</b>",
            f"🟢 کاربران آنلاین: <b>{online}</b>",
            f"📊 مجموع ترافیک مصرفی: <b>{escape(await format_traffic_size(usage))}</b>",
            "",
            "<i>اطلاعات مستقیماً از Rebecca دریافت شده است.</i>",
        ]
        await callback.message.edit_text("\n".join(lines), reply_markup=_back_keyboard())
    except Exception:
        await callback.message.edit_text(
            "⚠️ دریافت گزارش زنده Rebecca موقتاً ناموفق بود. دوباره تلاش کنید.",
            reply_markup=_back_keyboard(),
        )
    await callback.answer()


def _user_status_label(user: dict[str, Any]) -> str:
    status = str(user.get("status", "")).lower()
    if bool(user.get("is_online")):
        return "🟢 آنلاین"
    return {
        "active": "✅ فعال",
        "disabled": "⛔ غیرفعال",
        "expired": "⌛ منقضی",
        "limited": "📦 اتمام حجم",
        "quota_full": "📦 اتمام حجم",
        "on_hold": "⏸ در انتظار",
    }.get(status, f"ℹ️ {status or 'نامشخص'}")


async def _render_users(callback: CallbackQuery, admin: Any, page: int = 0) -> None:
    try:
        page = max(0, min(int(page), 100000))
        page_size = 10
        snapshot = await _admin_users_snapshot(str(admin.marzban_username or ""), limit=page_size, offset=page * page_size)
        users = snapshot["users"]
        total = _as_non_negative_int(snapshot.get("total"), len(users))
        panel_name = escape(str(admin.admin_name or admin.marzban_username or "-"))
        lines = [f"👥 <b>لیست کاربران پنل: {panel_name}</b>", ""]
        if not users:
            lines.append("• هیچ کاربری در Rebecca برای این ادمین یافت نشد.")
        else:
            for user in users:
                username = escape(str(user.get("username") or "-"))
                used = await format_traffic_size(_as_non_negative_int(user.get("used_traffic")))
                data_limit = user.get("data_limit")
                limit_text = "نامحدود"
                if isinstance(data_limit, int) and not isinstance(data_limit, bool) and data_limit > 0:
                    limit_text = await format_traffic_size(data_limit)
                service_name = str(user.get("service_name") or "").strip()
                service_part = f" · {escape(service_name)}" if service_name else ""
                remaining, expires_at = _expires_text(user.get("expire"))
                lines.extend([
                    f"• <code>{username}</code> — {_user_status_label(user)}{service_part}",
                    f"  ↳ مصرف: <b>{escape(used)}</b> / {escape(limit_text)}",
                    f"  ↳ اعتبار: <b>{escape(remaining)}</b>" if expires_at != "—" else "  ↳ اعتبار: نامحدود/نامشخص",
                ])
            lines.append(f"\nصفحه <b>{page + 1}</b> از <b>{max(1, (total + page_size - 1) // page_size)}</b>")
        lines.append(f"\nمجموع کاربران Rebecca: <b>{total}</b>")
        buttons = []
        if page > 0:
            buttons.append(InlineKeyboardButton(text="◀️ قبلی", callback_data=f"rebecca_users_page:{int(admin.id)}:{page-1}"))
        if (page + 1) * page_size < total:
            buttons.append(InlineKeyboardButton(text="بعدی ▶️", callback_data=f"rebecca_users_page:{int(admin.id)}:{page+1}"))
        keyboard = ([buttons] if buttons else []) + _back_keyboard().inline_keyboard
        await callback.message.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard))
    except Exception:
        await callback.message.edit_text(
            "⚠️ دریافت لیست کاربران Rebecca موقتاً ناموفق بود. دوباره تلاش کنید.",
            reply_markup=_back_keyboard(),
        )
    await callback.answer()


async def _owned_active_admins(user_id: int) -> list[Any]:
    # Read-only panel information remains available after expiry/suspension.
    # This does not authorize provisioning or any state-changing operation.
    return await db.get_admins_for_user(int(user_id))


async def _select_or_render(callback: CallbackQuery, action: str) -> None:
    admins = await _owned_active_admins(callback.from_user.id)
    if not admins:
        await callback.answer("هیچ پنل متصل‌شده‌ای برای شما یافت نشد.", show_alert=True)
        return
    renderers = {"info": _render_info, "report": _render_report, "users": _render_users}
    if len(admins) == 1:
        await renderers[action](callback, admins[0])
        return
    rows = []
    for admin in admins:
        name = str(admin.admin_name or admin.marzban_username or f"Panel {admin.id}")
        rows.append([InlineKeyboardButton(
            text=f"{'✅' if admin.is_active else '⏸'} {name}", callback_data=f"{action}_panel_{int(admin.id)}"
        )])
    rows.append([InlineKeyboardButton(text=config.BUTTONS["back"], callback_data="back_to_admin_main")])
    await callback.message.edit_text(
        f"🔹 شما {len(admins)} پنل متصل دارید. لطفاً یکی را برای مشاهده انتخاب کنید:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


async def _selected_admin(callback: CallbackQuery, prefix: str) -> Any | None:
    try:
        admin_id = int((callback.data or "").removeprefix(prefix))
    except (TypeError, ValueError):
        await callback.answer("پنل نامعتبر است.", show_alert=True)
        return None
    admin = await db.get_admin_by_id(admin_id)
    if not admin or int(admin.user_id) != int(callback.from_user.id):
        await callback.answer("پنل یافت نشد.", show_alert=True)
        return None
    return admin


@rebecca_admin_sync_router.callback_query(F.data == "my_info")
async def rebecca_my_info(callback: CallbackQuery):
    await _select_or_render(callback, "info")


@rebecca_admin_sync_router.callback_query(F.data == "my_report")
async def rebecca_my_report(callback: CallbackQuery):
    await _select_or_render(callback, "report")


@rebecca_admin_sync_router.callback_query(F.data == "my_users")
async def rebecca_my_users(callback: CallbackQuery):
    await _select_or_render(callback, "users")


@rebecca_admin_sync_router.callback_query(F.data.startswith("info_panel_"))
async def rebecca_info_panel(callback: CallbackQuery):
    admin = await _selected_admin(callback, "info_panel_")
    if admin:
        await _render_info(callback, admin)


@rebecca_admin_sync_router.callback_query(F.data.startswith("report_panel_"))
async def rebecca_report_panel(callback: CallbackQuery):
    admin = await _selected_admin(callback, "report_panel_")
    if admin:
        await _render_report(callback, admin)


@rebecca_admin_sync_router.callback_query(F.data.startswith("users_panel_"))
async def rebecca_users_panel(callback: CallbackQuery):
    admin = await _selected_admin(callback, "users_panel_")
    if admin:
        await _render_users(callback, admin)


@rebecca_admin_sync_router.callback_query(F.data.startswith("rebecca_users_page:"))
async def rebecca_users_page(callback: CallbackQuery) -> None:
    """Page through only the panels owned by the requesting Telegram user."""
    try:
        _, raw_id, raw_page = (callback.data or "").split(":")
        admin_id, page = int(raw_id), int(raw_page)
        if page < 0 or page > 100000:
            raise ValueError("invalid page")
    except (ValueError, TypeError):
        await callback.answer("صفحه نامعتبر است.", show_alert=True)
        return
    admin = await db.get_admin_by_id(admin_id)
    if not admin or int(admin.user_id) != int(callback.from_user.id):
        await callback.answer("دسترسی به این پنل مجاز نیست.", show_alert=True)
        return
    await _render_users(callback, admin, page=page)
