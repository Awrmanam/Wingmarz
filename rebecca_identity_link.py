from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import config
from database import db
from models.schemas import AdminModel
from rebecca_api import RebeccaAPIError, rebecca_api
from rebecca_identity_conflicts import rebecca_identity_conflicts
import logging

logger = logging.getLogger(__name__)


def _telegram_id(remote: dict[str, Any]) -> int | None:
    value = remote.get("telegram_id")
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _non_negative_int(value: Any, default: int = 0) -> int:
    if value is None or isinstance(value, bool):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(0, parsed)


def _validity_days(remote: dict[str, Any]) -> int:
    expire = _non_negative_int(remote.get("expire"))
    if expire <= 0:
        return 36500
    now = int(datetime.now(timezone.utc).timestamp())
    remaining = max(0, expire - now)
    return max(1, min(36500, (remaining + 86399) // 86400))


async def _sync_one(
    remote: dict[str, Any],
    *,
    telegram_username: str | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
) -> int | None:
    user_id = _telegram_id(remote)
    username = str(remote.get("username") or "").strip()
    role = str(remote.get("role") or "").strip().lower()
    if not user_id or not username or role != "standard":
        return None

    status = str(remote.get("status") or "").strip().lower()
    # No missing field is silently turned into a restrictive one-user quota.
    # The local model represents Rebecca's unlimited (0) limit as 1_000_000.
    raw_users_limit = remote.get("users_limit")
    max_users = None
    if raw_users_limit is not None:
        parsed = _non_negative_int(raw_users_limit, 0)
        max_users = parsed if parsed > 0 else 1_000_000
    data_limit = (
        _non_negative_int(remote.get("data_limit"))
        if remote.get("data_limit") is not None else None
    )

    async def safe_update(existing: AdminModel) -> int | None:
        if int(existing.user_id) != user_id:
            # A change upstream does not constitute authorization to move a
            # customer's local panel, orders or associated Telegram access.
            await rebecca_identity_conflicts.report(
                username, int(existing.user_id), int(user_id)
            )
            logger.warning(
                "Rebecca ownership conflict for %s: local=%s remote=%s; "
                "ownership unchanged, operator review required",
                username, int(existing.user_id), user_id,
            )
            return None

        await rebecca_identity_conflicts.clear_when_matching(username, user_id)
        updates: dict[str, Any] = {
            "login_url": (
                config.REBECCA_LOGIN_URL or config.REBECCA_URL or existing.login_url
            )
        }
        if status in {"active", "disabled"}:
            updates["is_active"] = 1 if status == "active" else 0
        if max_users is not None:
            updates["max_users"] = max_users
        if data_limit is not None:
            updates["max_total_traffic"] = data_limit
        if "expire" in remote:
            updates["validity_days"] = _validity_days(remote)
        if telegram_username:
            updates["username"] = telegram_username
        if first_name:
            updates["first_name"] = first_name
        if last_name:
            updates["last_name"] = last_name
        # Deliberately NEVER set user_id in a background sync.
        if not await db.update_admin(existing.id, **updates):
            return None
        return int(existing.id)

    existing = await db.get_admin_by_marzban_username(username)
    if existing:
        return await safe_update(existing)

    model = AdminModel(
        user_id=user_id,
        admin_name=username,
        marzban_username=username,
        marzban_password=None,
        login_url=config.REBECCA_LOGIN_URL or config.REBECCA_URL or None,
        username=telegram_username,
        first_name=first_name,
        last_name=last_name,
        max_users=max_users if max_users is not None else 1_000_000,
        max_total_time=0,
        max_total_traffic=data_limit if data_limit is not None else 0,
        validity_days=_validity_days(remote),
        is_active=status == "active",
    )
    if not await db.add_admin(model):
        # Another /start may have inserted this username. Re-read and apply
        # the exact same ownership checks rather than taking over its owner.
        existing = await db.get_admin_by_marzban_username(username)
        return await safe_update(existing) if existing else None

    created = await db.get_admin_by_marzban_username(username)
    return int(created.id) if created and created.id is not None else None

async def sync_rebecca_admins_for_telegram(
    telegram_id: int,
    *,
    telegram_username: str | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
) -> list[int]:
    """Auto-link all Rebecca standard admins assigned to this Telegram ID."""
    if config.PANEL_PROVIDER != "rebecca":
        return []
    try:
        remotes = await rebecca_api.list_admins()
    except RebeccaAPIError:
        return []

    linked: list[int] = []
    for remote in remotes:
        if _telegram_id(remote) != int(telegram_id):
            continue
        if str(remote.get("role") or "").strip().lower() != "standard":
            continue
        admin_id = await _sync_one(
            remote,
            telegram_username=telegram_username,
            first_name=first_name,
            last_name=last_name,
        )
        if admin_id is not None:
            linked.append(admin_id)
    return linked


async def sync_all_rebecca_admin_identities() -> int:
    """Refresh the local identity mirror for every Rebecca standard admin with telegram_id."""
    if config.PANEL_PROVIDER != "rebecca":
        return 0
    try:
        remotes = await rebecca_api.list_admins()
    except RebeccaAPIError:
        return 0

    count = 0
    for remote in remotes:
        if _telegram_id(remote) is None:
            continue
        if str(remote.get("role") or "").strip().lower() != "standard":
            continue
        if await _sync_one(remote) is not None:
            count += 1
    return count
