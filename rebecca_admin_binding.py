"""Explicit owner-approved linking of existing Rebecca standard admins.

No password is recovered or requested. This helper never transfers an existing
local/remote owner: conflicts require a separately audited migration workflow.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from authorization import is_owner
from database import db
from group_ops_service import group_ops_service
from rebecca_api import rebecca_api
from rebecca_identity_link import _sync_one, _telegram_id
import config

logger = logging.getLogger(__name__)


class RebeccaBindingError(ValueError):
    pass


@dataclass(frozen=True)
class BindingPreview:
    username: str
    target_telegram_id: int
    current_remote_id: int | None
    current_local_id: int | None
    status: str


async def preview_binding(username: str, telegram_id: int) -> BindingPreview:
    if config.PANEL_PROVIDER != "rebecca":
        raise RebeccaBindingError("اتصال بدون رمز فقط در حالت Rebecca قابل استفاده است.")
    username = str(username or "").strip()
    target_id = int(telegram_id)
    if not username or target_id <= 0:
        raise RebeccaBindingError("نام پنل یا Telegram ID عددی نامعتبر است.")

    remote = await rebecca_api.find_admin(username)
    if not remote or str(remote.get("username") or "") != username:
        raise RebeccaBindingError("پنل در Rebecca پیدا نشد.")
    if str(remote.get("role") or "").strip().lower() != "standard":
        raise RebeccaBindingError("فقط ادمین‌های استاندارد Rebecca قابل اتصال‌اند.")
    remote_id = _telegram_id(remote)
    local = await db.get_admin_by_marzban_username(username)
    local_id = int(local.user_id) if local else None
    if local_id is not None and local_id != target_id:
        raise RebeccaBindingError(
            "این پنل قبلاً در Wingmarz به Telegram ID دیگری متصل است؛ "
            "تغییر مالکیت به بررسی و انتقال جداگانه نیاز دارد."
        )
    if remote_id is not None and remote_id != target_id:
        raise RebeccaBindingError(
            "این پنل در Rebecca به Telegram ID دیگری متصل است؛ "
            "تغییر مالکیت بدون بررسی مستقل مجاز نیست."
        )

    return BindingPreview(
        username=username,
        target_telegram_id=target_id,
        current_remote_id=remote_id,
        current_local_id=local_id,
        status=str(remote.get("status") or "unknown"),
    )


async def bind_existing_admin(username: str, telegram_id: int, *, actor_id: int) -> int:
    """Link one panel after an explicit Owner confirmation; safely replayable."""
    if not is_owner(int(actor_id)):
        raise PermissionError("فقط مالک اصلی می‌تواند ادمین Rebecca را متصل کند.")
    before = await preview_binding(username, telegram_id)
    if before.current_remote_id is None:
        # This PUT can return an ambiguous timeout even if Rebecca accepted it;
        # never overwrite a different owner. Retry only the read side.
        try:
            await rebecca_api.update_admin_fields(
                before.username, {"telegram_id": before.target_telegram_id}
            )
        except Exception as exc:
            actual = await rebecca_api.find_admin(before.username)
            if not actual or _telegram_id(actual) != before.target_telegram_id:
                raise RebeccaBindingError(
                    "وضعیت ثبت Telegram ID در Rebecca نامشخص است؛ "
                    "قبل از هر تغییر دوباره، اطلاعات پنل بررسی شود."
                ) from exc

    confirmed = await preview_binding(before.username, before.target_telegram_id)
    if confirmed.current_remote_id != before.target_telegram_id:
        raise RebeccaBindingError("Rebecca مالکیت Telegram ID جدید را تأیید نکرد.")
    remote = await rebecca_api.find_admin(before.username)
    if not remote or _telegram_id(remote) != before.target_telegram_id:
        raise RebeccaBindingError("نتیجه اتصال Rebecca معتبر نیست.")

    admin_id = await _sync_one(remote)
    if admin_id is None:
        raise RebeccaBindingError(
            "Telegram ID در Rebecca ثبت شد، اما ثبت پنل در Wingmarz کامل نشده؛ "
            "دوباره فقط اطلاعات اتصال را بررسی کنید."
        )

    try:
        await group_ops_service.audit(
            actor_id=int(actor_id),
            action="rebecca.admin.link_by_telegram_id",
            subject_type="admin",
            subject_id=int(admin_id),
            old_value={
                "username": before.username,
                "local_telegram_id": before.current_local_id,
                "remote_telegram_id": before.current_remote_id,
            },
            new_value={
                "username": before.username,
                "telegram_id": before.target_telegram_id,
            },
        )
    except Exception:
        logger.exception("Rebecca bind succeeded but audit delivery failed (admin ID %s)", admin_id)
    return int(admin_id)
