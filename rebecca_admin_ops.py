from __future__ import annotations

from datetime import datetime, timezone
from html import escape
from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

import config
from database import db
from group_ops_service import group_ops_service
from rebecca_api import RebeccaAPIError, rebecca_api


PERCENT_THRESHOLDS = (70, 80, 90, 95, 100)
TIME_THRESHOLDS = (
    (7 * 86400, "۷ روز"),
    (3 * 86400, "۳ روز"),
    (1 * 86400, "۱ روز"),
    (6 * 3600, "۶ ساعت"),
    (0, "اتمام"),
)


def _percent_rank(value_percent: float) -> int:
    rank = 0
    for idx, threshold in enumerate(PERCENT_THRESHOLDS, start=1):
        if value_percent >= threshold:
            rank = idx
    return rank


def _time_rank(remaining_seconds: float) -> int:
    rank = 0
    for idx, (threshold, _label) in enumerate(TIME_THRESHOLDS, start=1):
        if remaining_seconds <= threshold:
            rank = idx
    return rank


def _bytes(value: int) -> str:
    n = max(0, int(value or 0))
    units = ("B", "KB", "MB", "GB", "TB")
    f = float(n)
    for unit in units:
        if f < 1024 or unit == units[-1]:
            return f"{f:.2f} {unit}" if unit != "B" else f"{int(f)} B"
        f /= 1024
    return f"{n} B"


async def _send_admin_alert(bot, admin, title: str, body: str) -> bool:
    group_kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="⚙️ مدیریت کامل", callback_data=f"gops:admin:{admin.id}")
    ]])
    private_kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🔄 تمدید / افزایش", callback_data=f"admin_renew_panel_{admin.id}"),
        InlineKeyboardButton(text="ℹ️ اطلاعات", callback_data=f"info_panel_{admin.id}"),
    ]])
    panel_name = escape(str(admin.admin_name or admin.marzban_username or admin.id))
    username = escape(str(admin.marzban_username or "-"))
    common = (
        f"{title}\n\n"
        f"👤 <b>{panel_name}</b>\n"
        f"🆔 Telegram: <code>{int(admin.user_id)}</code>\n"
        f"🔐 Rebecca: <code>{username}</code>\n\n"
        f"{body}"
    )
    group_ok = await group_ops_service.send_text(bot, "admins", common, reply_markup=group_kb)
    private_ok = False
    try:
        await bot.send_message(int(admin.user_id), common, reply_markup=private_kb)
        private_ok = True
    except Exception:
        pass
    return group_ok or private_ok


async def _maybe_percent_alert(bot, admin, metric: str, percent: float, title: str, body: str) -> None:
    percent = max(0.0, float(percent))
    state = await group_ops_service.get_alert_state(int(admin.id), metric)
    previous_value = float((state or {}).get("last_value") or 0.0)
    previous_rank = int((state or {}).get("last_rank") or 0)
    if state and percent + 5 < previous_value:
        previous_rank = 0
    rank = _percent_rank(percent)
    if rank > previous_rank:
        if await _send_admin_alert(bot, admin, title, body):
            await group_ops_service.mark_alert_state(int(admin.id), metric, percent, rank)
    elif not state or abs(percent - previous_value) >= 1:
        await group_ops_service.mark_alert_state(int(admin.id), metric, percent, previous_rank)


async def _maybe_time_alert(bot, admin, remaining: float, body: str) -> None:
    state = await group_ops_service.get_alert_state(int(admin.id), "time")
    previous_value = float((state or {}).get("last_value") or remaining)
    previous_rank = int((state or {}).get("last_rank") or 0)
    # A meaningful increase means the admin was renewed; allow the threshold
    # sequence to start over for the new validity window.
    if state and remaining > previous_value + 12 * 3600:
        previous_rank = 0
    rank = _time_rank(remaining)
    if rank > previous_rank:
        if await _send_admin_alert(bot, admin, "⏰ <b>هشدار زمان Admin</b>", body):
            await group_ops_service.mark_alert_state(int(admin.id), "time", remaining, rank)
    elif not state or abs(remaining - previous_value) >= 3600:
        await group_ops_service.mark_alert_state(int(admin.id), "time", remaining, previous_rank)


async def sync_rebecca_admin_alerts(bot) -> None:
    """Check Rebecca limits and send deduplicated alerts to user + Admins topic."""
    if config.PANEL_PROVIDER != "rebecca":
        return
    admins = [a for a in await db.get_all_admins() if a.is_active and a.id and a.marzban_username]
    now = datetime.now(timezone.utc).timestamp()
    for admin in admins:
        try:
            remote = await rebecca_api.find_admin(str(admin.marzban_username))
            if not remote:
                continue
            usage = await rebecca_api.get_admin_usage(str(admin.marzban_username))
            data_limit = remote.get("data_limit")
            if isinstance(data_limit, int) and data_limit > 0:
                percent = usage * 100 / data_limit
                await _maybe_percent_alert(
                    bot,
                    admin,
                    "traffic",
                    percent,
                    "📊 <b>هشدار حجم Admin</b>",
                    f"مصرف: <b>{_bytes(usage)} / {_bytes(data_limit)}</b>\n"
                    f"درصد مصرف: <b>{percent:.1f}%</b>",
                )

            users_limit = remote.get("users_limit")
            if isinstance(users_limit, int) and users_limit > 0:
                summary = await rebecca_api.get_admin_users_summary(str(admin.marzban_username), limit=1)
                total = int(summary.get("total") or 0)
                percent = total * 100 / users_limit
                await _maybe_percent_alert(
                    bot,
                    admin,
                    "users",
                    percent,
                    "👥 <b>هشدار ظرفیت کاربران Admin</b>",
                    f"کاربران: <b>{total} / {users_limit}</b>\n"
                    f"ظرفیت مصرف‌شده: <b>{percent:.1f}%</b>",
                )

            expire = remote.get("expire")
            if isinstance(expire, int) and expire > 0:
                remaining = float(expire - now)
                if remaining <= 7 * 86400:
                    if remaining <= 0:
                        label = "منقضی شده"
                    elif remaining < 86400:
                        label = f"{max(0, int(remaining // 3600))} ساعت"
                    else:
                        label = f"{max(0, int(remaining // 86400))} روز"
                    await _maybe_time_alert(
                        bot,
                        admin,
                        remaining,
                        f"زمان باقی‌مانده: <b>{escape(label)}</b>",
                    )
                else:
                    state = await group_ops_service.get_alert_state(int(admin.id), "time")
                    prev_rank = int((state or {}).get("last_rank") or 0)
                    prev_value = float((state or {}).get("last_value") or remaining)
                    rank = 0 if remaining > prev_value + 12 * 3600 else prev_rank
                    await group_ops_service.mark_alert_state(int(admin.id), "time", remaining, rank)
        except RebeccaAPIError:
            continue
        except Exception as exc:
            await group_ops_service.report_error(
                bot,
                exc,
                context=f"admin-alert {admin.marzban_username}",
            )
