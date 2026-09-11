"""Owner-bound restoration of an already-issued panel trial."""
from __future__ import annotations

import time
import aiosqlite

import config
from database import db
from trial_experience_service import trial_experience_service


async def load_active_panel_trial(user_id: int):
    """Return the caller's newest unexpired panel trial without issuing anything."""
    await trial_experience_service.ensure_schema()
    now = int(time.time())
    async with aiosqlite.connect(config.DATABASE_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            """
            SELECT user_id,provider,provider_username,plan_id,expire_at,created_at
            FROM panel_trial_issues
            WHERE user_id=? AND expire_at>?
            ORDER BY id DESC
            LIMIT 1
            """,
            (int(user_id), now),
        ) as cur:
            row = await cur.fetchone()
    if not row:
        return None

    username = str(row["provider_username"] or "").strip()
    if not username:
        return None
    local = await db.get_admin_by_marzban_username(username)
    if local is None or int(local.user_id) != int(user_id):
        return None

    login_url = await db.get_setting("global_login_url")
    if not login_url:
        login_url = (
            (getattr(config, "REBECCA_LOGIN_URL", "") or getattr(config, "REBECCA_URL", ""))
            if str(row["provider"]).lower() == "rebecca"
            else getattr(config, "MARZBAN_URL", "")
        )

    return {
        "provider": str(row["provider"]),
        "username": username,
        "password": str(local.marzban_password or ""),
        "plan_id": int(row["plan_id"] or 0),
        "plan_name": str(local.admin_name or "پنل تست"),
        "login_url": str(login_url or ""),
        "expire_at": int(row["expire_at"]),
        "traffic_bytes": int(local.max_total_traffic or 0),
        "max_users": int(local.max_users or 0),
        "reopened": True,
    }
