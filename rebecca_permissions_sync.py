"""Best-effort security reconciliation for existing Wingmarz-managed Rebecca admins."""
from __future__ import annotations

import aiosqlite

import config
from rebecca_api import RebeccaAPIError, rebecca_api


async def reconcile_managed_admin_permissions() -> tuple[int, int]:
    """Force all locally-managed Rebecca accounts to the user-only Standard profile.

    Returns ``(updated, failed)``. Provider outages never prevent the bot from
    starting; newly-created accounts are already protected synchronously by
    ``RebeccaAPI.create_admin_verified``.
    """
    if str(config.PANEL_PROVIDER or "").lower() != "rebecca":
        return 0, 0
    try:
        async with aiosqlite.connect(config.DATABASE_PATH) as conn:
            async with conn.execute(
                """
                SELECT DISTINCT marzban_username
                FROM admins
                WHERE marzban_username IS NOT NULL AND TRIM(marzban_username)<>''
                ORDER BY id
                """
            ) as cur:
                usernames = [str(row[0]).strip() for row in await cur.fetchall() if str(row[0]).strip()]
    except aiosqlite.OperationalError:
        return 0, 0

    updated = failed = 0
    for username in usernames:
        try:
            await rebecca_api.enforce_standard_user_only(username)
            updated += 1
        except (RebeccaAPIError, ValueError, TypeError):
            failed += 1
    return updated, failed
