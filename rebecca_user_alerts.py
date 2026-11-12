"""Rebecca per-user quota/expiry monitoring with durable smart grouped alerts.

Read-only provider polling, scoped to the immutable local Telegram owner.
No delete/disable/renew RPCs are ever issued. An initial scan establishes a
baseline; new transitions create one event per episode, not per scheduler tick.
"""
from __future__ import annotations

from collections import defaultdict
from html import escape
import logging
import time
import uuid
from typing import Any

import aiosqlite

import config
from database import db
from premium_ui_service import premium_ui_service
from rebecca_api import rebecca_api

logger = logging.getLogger(__name__)

PAGE_SIZE = 200
MAX_USERS_PER_PANEL = 10000
MAX_PANELS_PER_TICK = 20
MAX_EVENTS_PER_DELIVERY = 100
MAX_ITEMS_PER_DIGEST = 12


def _nonneg(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def classify_user(user: dict[str, Any], now: int) -> str:
    """Never interpret a manually disabled user or API error as quota-full."""
    status = str(user.get("status") or "").strip().lower()
    if status in {"disabled", "deleted", "on_hold"}:
        return status
    if status == "expired":
        return "expired"
    expiry = _nonneg(user.get("expire"))
    if expiry and expiry <= now:
        return "expired"
    if status == "quota_full":
        return "quota_full"
    used, limit = _nonneg(user.get("used_traffic")), _nonneg(user.get("data_limit"))
    if limit is not None and limit > 0 and used is not None and used >= limit:
        return "quota_full"
    return "active" if status in {"active", ""} else status


async def _all_scoped_users(panel_username: str) -> list[dict[str, Any]]:
    """Fetch ALL pages; never persist an incomplete partial snapshot."""
    result: list[dict[str, Any]] = []
    offset = 0
    total: int | None = None
    while True:
        payload = await rebecca_api._request(
            "GET",
            "/api/users",
            params={
                "admin": str(panel_username),
                "limit": PAGE_SIZE,
                "offset": offset,
                "sort": "username",
            },
        )
        if not isinstance(payload, dict):
            raise ValueError("Rebecca users response is not an object")
        page = payload.get("users")
        count = payload.get("total")
        if not isinstance(page, list) or any(not isinstance(u, dict) for u in page):
            raise ValueError("Rebecca users response is malformed")
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValueError("Rebecca users total is invalid")
        if total is None:
            total = count
            if total > MAX_USERS_PER_PANEL:
                raise ValueError("Rebecca panel has more users than safe scan cap")
        elif count != total:
            # Pagination during churn is unsafe for transition detection.
            raise ValueError("Rebecca users count changed during scan")
        for user in page:
            # The global Rebecca bearer must never leak another reseller's
            # users even if provider-side scoping is configured incorrectly.
            owner = user.get("admin")
            if owner is not None and str(owner) != str(panel_username):
                raise ValueError("Rebecca returned a user outside requested admin")
        result.extend(page)
        offset += len(page)
        if offset >= total:
            if offset != total:
                raise ValueError("Rebecca users count changed during scan")
            return result
        if not page:
            raise ValueError("Rebecca returned an empty partial users page")
        if offset > MAX_USERS_PER_PANEL:
            raise ValueError("Rebecca users page cap exceeded")


class RebeccaUserAlerts:
    def __init__(self, db_path: str | None = None):
        self._db_path = db_path

    @property
    def db_path(self) -> str:
        return self._db_path or config.DATABASE_PATH

    async def ensure_schema(self) -> None:
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.executescript("""
                CREATE TABLE IF NOT EXISTS rebecca_user_alert_watch (
                    panel_username TEXT NOT NULL,
                    user_username TEXT NOT NULL,
                    owner_telegram_id INTEGER NOT NULL,
                    user_state TEXT NOT NULL,
                    episode INTEGER NOT NULL DEFAULT 0,
                    used_bytes INTEGER,
                    limit_bytes INTEGER,
                    last_seen_at INTEGER NOT NULL,
                    PRIMARY KEY(panel_username,user_username)
                );
                CREATE TABLE IF NOT EXISTS rebecca_user_alert_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_key TEXT NOT NULL UNIQUE,
                    owner_telegram_id INTEGER NOT NULL,
                    panel_username TEXT NOT NULL,
                    user_username TEXT NOT NULL,
                    event_type TEXT NOT NULL
                        CHECK(event_type IN ('quota_full','expired')),
                    episode INTEGER NOT NULL,
                    used_bytes INTEGER,
                    limit_bytes INTEGER,
                    delivery_state TEXT NOT NULL DEFAULT 'pending'
                        CHECK(delivery_state IN ('pending','sending','sent')),
                    attempts INTEGER NOT NULL DEFAULT 0,
                    retry_at INTEGER NOT NULL DEFAULT 0,
                    lease_token TEXT,
                    lease_until INTEGER NOT NULL DEFAULT 0,
                    occurred_at INTEGER NOT NULL,
                    delivered_at INTEGER
                );
                CREATE INDEX IF NOT EXISTS idx_rebecca_user_alert_queue
                ON rebecca_user_alert_events(delivery_state,retry_at,occurred_at);
                CREATE TABLE IF NOT EXISTS rebecca_user_alert_panel_poll (
                    panel_username TEXT PRIMARY KEY,
                    last_attempt INTEGER NOT NULL DEFAULT 0
                );
            """)
            await conn.commit()

    async def poll_candidates(self, admins: list, max_panels: int = MAX_PANELS_PER_TICK) -> list:
        """Round-robin candidates, avoiding starvation at large reseller counts."""
        await self.ensure_schema()
        eligible = {
            str(a.marzban_username): a
            for a in admins
            if a.marzban_username and int(a.user_id) > 0 and bool(a.is_active)
        }
        if not eligible:
            return []
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute("BEGIN IMMEDIATE")
            for username in eligible:
                await conn.execute(
                    "INSERT OR IGNORE INTO rebecca_user_alert_panel_poll(panel_username) VALUES(?)",
                    (username,),
                )
            marks = ",".join("?" for _ in eligible)
            async with conn.execute(
                f"""SELECT panel_username FROM rebecca_user_alert_panel_poll
                    WHERE panel_username IN ({marks})
                    ORDER BY last_attempt ASC,panel_username ASC LIMIT ?""",
                (*eligible.keys(), max(1, min(int(max_panels), 100))),
            ) as cur:
                names = [str(row[0]) for row in await cur.fetchall()]
            stamp = int(time.time())
            for name in names:
                await conn.execute(
                    "UPDATE rebecca_user_alert_panel_poll SET last_attempt=? WHERE panel_username=?",
                    (stamp, name),
                )
            await conn.commit()
        return [eligible[n] for n in names]

    async def observe_panel(
        self, panel_username: str, owner_id: int,
        users: list[dict[str, Any]], now: int | None = None,
    ) -> int:
        """Persist transitions only after a complete provider snapshot arrives."""
        now = int(now if now is not None else time.time())
        owner_id = int(owner_id)
        if owner_id <= 0:
            raise ValueError("Telegram owner ID must be positive")
        await self.ensure_schema()
        new_events = 0
        seen: set[str] = set()
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute("BEGIN IMMEDIATE")
            for user in users:
                username = str(user.get("username") or "").strip()
                if not username or username in seen:
                    await conn.rollback()
                    raise ValueError("Rebecca username missing/duplicated in snapshot")
                seen.add(username)
                new_state = classify_user(user, now)
                used = _nonneg(user.get("used_traffic"))
                limit = _nonneg(user.get("data_limit"))
                async with conn.execute(
                    """SELECT owner_telegram_id,user_state,episode
                       FROM rebecca_user_alert_watch
                       WHERE panel_username=? AND user_username=?""",
                    (str(panel_username), username),
                ) as cur:
                    row = await cur.fetchone()
                if row is None:
                    # Baseline. Existing exhausted accounts do not masquerade
                    # as fresh events just because monitoring was installed.
                    await conn.execute("""
                        INSERT INTO rebecca_user_alert_watch(
                          panel_username,user_username,owner_telegram_id,
                          user_state,episode,used_bytes,limit_bytes,last_seen_at
                        ) VALUES(?,?,?,?,0,?,?,?)
                    """, (panel_username, username, owner_id, new_state, used, limit, now))
                    continue
                prior_owner, old_state, old_episode = int(row[0]), str(row[1]), int(row[2])
                if prior_owner != owner_id:
                    # A new/changed owner never inherits old private alerts.
                    # Ownership requires separate audited reconciliation.
                    await conn.rollback()
                    raise ValueError("Panel user alert owner mismatch")
                episode = old_episode
                if old_state != new_state and new_state in {"quota_full", "expired"}:
                    episode += 1
                    await conn.execute("""
                        INSERT OR IGNORE INTO rebecca_user_alert_events(
                          event_key,owner_telegram_id,panel_username,user_username,
                          event_type,episode,used_bytes,limit_bytes,occurred_at
                        ) VALUES(?,?,?,?,?,?,?,?,?)
                    """, (
                        f"{panel_username}:{username}:{new_state}:{episode}",
                        owner_id, panel_username, username, new_state,
                        episode, used, limit, now,
                    ))
                    new_events += 1
                await conn.execute("""
                    UPDATE rebecca_user_alert_watch SET
                      user_state=?,episode=?,used_bytes=?,limit_bytes=?,last_seen_at=?
                    WHERE panel_username=? AND user_username=?
                """, (new_state, episode, used, limit, now, panel_username, username))
            await conn.commit()
        return new_events

    async def claim_due(self, now: int, window: int = 60) -> tuple[str, list[dict]]:
        """Lease durable due events; pending/expired leases remain retriable."""
        await self.ensure_schema()
        token = uuid.uuid4().hex
        cutoff = int(now) - max(0, int(window))
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            await conn.execute("BEGIN IMMEDIATE")
            async with conn.execute("""
                SELECT * FROM rebecca_user_alert_events
                WHERE occurred_at<=? AND retry_at<=?
                  AND (delivery_state='pending' OR
                       (delivery_state='sending' AND lease_until<?))
                ORDER BY owner_telegram_id,occurred_at,id LIMIT ?
            """, (cutoff, int(now), int(now), MAX_EVENTS_PER_DELIVERY)) as cur:
                rows = [dict(row) for row in await cur.fetchall()]
            for row in rows:
                await conn.execute("""
                    UPDATE rebecca_user_alert_events SET
                        delivery_state='sending',lease_token=?,lease_until=?
                    WHERE id=?
                """, (token, int(now)+300, int(row["id"])))
            await conn.commit()
        return token, rows

    async def mark_sent(self, ids: list[int], token: str, now: int) -> None:
        await self._finish(ids, token, now, delivered=True)

    async def retry_later(self, ids: list[int], token: str, now: int) -> None:
        await self._finish(ids, token, now, delivered=False)

    async def _finish(self, ids: list[int], token: str, now: int, *, delivered: bool) -> None:
        if not ids:
            return
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute("BEGIN IMMEDIATE")
            for event_id in ids:
                if delivered:
                    await conn.execute("""
                        UPDATE rebecca_user_alert_events
                        SET delivery_state='sent',delivered_at=?,lease_token=NULL,
                            lease_until=0
                        WHERE id=? AND delivery_state='sending' AND lease_token=?
                    """, (int(now), int(event_id), token))
                else:
                    await conn.execute("""
                        UPDATE rebecca_user_alert_events
                        SET delivery_state='pending',attempts=attempts+1,
                            retry_at=? + MIN(3600,30*(1 << MIN(attempts,7))),
                            lease_token=NULL,lease_until=0
                        WHERE id=? AND delivery_state='sending' AND lease_token=?
                    """, (int(now), int(event_id), token))
            await conn.commit()

    async def deliver(self, bot, now: int | None = None) -> int:
        now = int(now if now is not None else time.time())
        token, rows = await self.claim_due(
            now, window=int(getattr(config, "REBECCA_USER_ALERTS_GROUP_WINDOW", 60))
        )
        groups: dict[int, list[dict]] = defaultdict(list)
        for item in rows:
            groups[int(item["owner_telegram_id"])].append(item)
        delivered = 0
        for owner_id, events in groups.items():
            blocks = [events[i:i+MAX_ITEMS_PER_DIGEST] for i in range(0, len(events), MAX_ITEMS_PER_DIGEST)]
            for block in blocks:
                # For 1-2 events preserve individual context; 3+ are grouped.
                messages = (
                    [block] if len(block) >= int(getattr(config, "REBECCA_USER_ALERTS_GROUP_MIN", 3))
                    else [[item] for item in block]
                )
                for chunk in messages:
                    ids = [int(item["id"]) for item in chunk]
                    try:
                        text = await self.render_alert(chunk)
                        await bot.send_message(owner_id, text, parse_mode="HTML")
                    except Exception:
                        logger.exception("Rebecca alert Telegram delivery failed for owner %s", owner_id)
                        await self.retry_later(ids, token, now)
                    else:
                        await self.mark_sent(ids, token, now)
                        delivered += len(ids)
        return delivered

    async def render_alert(self, events: list[dict]) -> str:
        def size(n: Any) -> str:
            return "نامحدود" if n is None else f"{int(n)/1024**3:.2f} GB"
        if len(events) == 1:
            item = events[0]
            key = "rebecca_user_quota_single" if item["event_type"] == "quota_full" else "rebecca_user_expired_single"
            tpl = config.MESSAGES[key]
            rendered = tpl.format(
                username=escape(str(item["user_username"])),
                panel_name=escape(str(item["panel_username"])),
                used_traffic=size(item["used_bytes"]),
                data_limit=size(item["limit_bytes"]),
            )
        else:
            lines = []
            for item in events:
                title = "اتمام حجم" if item["event_type"] == "quota_full" else "انقضا"
                lines.append(
                    f"• <code>{escape(str(item['user_username']))}</code> · "
                    f"{escape(str(item['panel_username']))} · {title}"
                )
            rendered = config.MESSAGES["rebecca_user_smart_digest"].format(
                count=len(events), users_list="\n".join(lines)
            )
        return await premium_ui_service.render_placeholders(str(rendered))

    async def tick(self, bot, now: int | None = None) -> dict[str, int]:
        """All network reads are scoped by Rebecca panel and immutable owner."""
        now = int(now if now is not None else time.time())
        if config.PANEL_PROVIDER != "rebecca":
            return {"polled": 0, "events": 0, "delivered": 0}
        local = await db.get_all_admins()
        remotes = await rebecca_api.list_admins()
        by_name = {
            str(a.get("username")): a for a in remotes
            if isinstance(a, dict) and a.get("username")
        }
        chosen = await self.poll_candidates(local)
        emitted = 0
        successful = 0
        for admin in chosen:
            panel = str(admin.marzban_username)
            remote = by_name.get(panel)
            if not remote or str(remote.get("role") or "").lower() != "standard":
                continue
            remote_id = _nonneg(remote.get("telegram_id"))
            if remote_id and remote_id != int(admin.user_id):
                logger.warning("Skipping Rebecca user scan for panel with ownership mismatch %s", panel)
                continue
            try:
                users = await _all_scoped_users(panel)
                emitted += await self.observe_panel(panel, int(admin.user_id), users, now)
                successful += 1
            except Exception:
                logger.exception("Rebecca user alert scan failed for panel %s", panel)
        delivered = await self.deliver(bot, now)
        return {"polled": successful, "events": emitted, "delivered": delivered}


rebecca_user_alerts = RebeccaUserAlerts()
