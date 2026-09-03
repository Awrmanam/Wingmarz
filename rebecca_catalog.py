"""Persistent Rebecca service catalog and read-only service discovery.

The catalog deliberately does not reference plans with a foreign key. Plans keep
storing Rebecca service IDs in their existing comma-separated field so removing
an item from this catalog never corrupts historical plans or orders.
"""
from __future__ import annotations

from dataclasses import dataclass
from html import escape
from typing import Any, Iterable

import aiosqlite

import config
from rebecca_api import RebeccaAPIError, rebecca_api


class RebeccaDiscoveryError(RuntimeError):
    """Rebecca returned data that cannot be safely used for discovery."""


class RebeccaDiscoveryNotFound(RebeccaDiscoveryError):
    """The requested Rebecca config/user does not exist."""


class RebeccaCatalogDuplicate(RuntimeError):
    """At least one Rebecca service is already present in the local catalog."""


@dataclass(slots=True)
class RebeccaServiceRecord:
    id: int
    rebecca_service_id: int
    display_name: str
    provider_name: str | None
    source_username: str | None
    is_enabled: bool
    created_at: str | None = None
    updated_at: str | None = None


def _row_to_record(row: aiosqlite.Row) -> RebeccaServiceRecord:
    return RebeccaServiceRecord(
        id=int(row["id"]),
        rebecca_service_id=int(row["rebecca_service_id"]),
        display_name=str(row["display_name"]),
        provider_name=row["provider_name"],
        source_username=row["source_username"],
        is_enabled=bool(row["is_enabled"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


async def init_catalog_table() -> None:
    async with aiosqlite.connect(config.DATABASE_PATH) as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS rebecca_services (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                rebecca_service_id INTEGER NOT NULL UNIQUE,
                display_name TEXT NOT NULL,
                provider_name TEXT,
                source_username TEXT,
                is_enabled INTEGER NOT NULL DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await conn.commit()


async def list_services(*, enabled_only: bool = False) -> list[RebeccaServiceRecord]:
    await init_catalog_table()
    sql = "SELECT * FROM rebecca_services"
    params: tuple[Any, ...] = ()
    if enabled_only:
        sql += " WHERE is_enabled = ?"
        params = (1,)
    sql += " ORDER BY display_name COLLATE NOCASE, rebecca_service_id"
    async with aiosqlite.connect(config.DATABASE_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(sql, params) as cur:
            rows = await cur.fetchall()
    return [_row_to_record(row) for row in rows]


async def get_service(internal_id: int) -> RebeccaServiceRecord | None:
    await init_catalog_table()
    async with aiosqlite.connect(config.DATABASE_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute("SELECT * FROM rebecca_services WHERE id = ?", (int(internal_id),)) as cur:
            row = await cur.fetchone()
    return _row_to_record(row) if row else None


async def get_service_by_rebecca_id(service_id: int) -> RebeccaServiceRecord | None:
    await init_catalog_table()
    async with aiosqlite.connect(config.DATABASE_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            "SELECT * FROM rebecca_services WHERE rebecca_service_id = ?", (int(service_id),)
        ) as cur:
            row = await cur.fetchone()
    return _row_to_record(row) if row else None


async def import_services_atomic(
    services: Iterable[dict[str, Any]], *, source_username: str | None
) -> list[RebeccaServiceRecord]:
    """Import all services or none of them.

    Each input item must contain a positive integer ``service_id`` and a non-empty
    ``display_name``. ``provider_name`` is optional. Duplicate provider IDs abort
    the whole transaction instead of partially importing a multi-service result.
    """
    normalized: list[tuple[int, str, str | None]] = []
    seen: set[int] = set()
    for item in services:
        service_id = item.get("service_id")
        if isinstance(service_id, bool) or not isinstance(service_id, int) or service_id <= 0:
            raise ValueError("Rebecca service ID must be a positive integer")
        if service_id in seen:
            raise RebeccaCatalogDuplicate("Duplicate Rebecca service ID in import")
        seen.add(service_id)
        display_name = str(item.get("display_name") or "").strip()
        if not display_name:
            raise ValueError("Display name is required")
        provider_name = item.get("provider_name")
        if provider_name is not None and not isinstance(provider_name, str):
            raise ValueError("Provider service name must be text or null")
        normalized.append((service_id, display_name, provider_name))
    if not normalized:
        raise ValueError("At least one service is required")

    await init_catalog_table()
    async with aiosqlite.connect(config.DATABASE_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        try:
            await conn.execute("BEGIN IMMEDIATE")
            placeholders = ",".join("?" for _ in normalized)
            ids = tuple(item[0] for item in normalized)
            async with conn.execute(
                f"SELECT rebecca_service_id FROM rebecca_services WHERE rebecca_service_id IN ({placeholders})",
                ids,
            ) as cur:
                existing = await cur.fetchall()
            if existing:
                raise RebeccaCatalogDuplicate("Rebecca service already exists in catalog")
            for service_id, display_name, provider_name in normalized:
                await conn.execute(
                    """
                    INSERT INTO rebecca_services
                        (rebecca_service_id, display_name, provider_name, source_username, is_enabled)
                    VALUES (?, ?, ?, ?, 1)
                    """,
                    (service_id, display_name, provider_name, source_username),
                )
            await conn.commit()
        except Exception:
            await conn.rollback()
            raise

        async with conn.execute(
            f"SELECT * FROM rebecca_services WHERE rebecca_service_id IN ({placeholders}) ORDER BY id", ids
        ) as cur:
            rows = await cur.fetchall()
    return [_row_to_record(row) for row in rows]


async def rename_service(internal_id: int, display_name: str) -> bool:
    name = str(display_name or "").strip()
    if not name:
        raise ValueError("Display name is required")
    await init_catalog_table()
    async with aiosqlite.connect(config.DATABASE_PATH) as conn:
        cur = await conn.execute(
            "UPDATE rebecca_services SET display_name = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (name, int(internal_id)),
        )
        await conn.commit()
        return cur.rowcount > 0


async def set_service_enabled(internal_id: int, enabled: bool) -> bool:
    await init_catalog_table()
    async with aiosqlite.connect(config.DATABASE_PATH) as conn:
        cur = await conn.execute(
            "UPDATE rebecca_services SET is_enabled = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (1 if enabled else 0, int(internal_id)),
        )
        await conn.commit()
        return cur.rowcount > 0


async def remove_service(internal_id: int) -> bool:
    await init_catalog_table()
    async with aiosqlite.connect(config.DATABASE_PATH) as conn:
        cur = await conn.execute("DELETE FROM rebecca_services WHERE id = ?", (int(internal_id),))
        await conn.commit()
        return cur.rowcount > 0


async def service_ids_from_catalog_ids(catalog_ids: Iterable[int]) -> list[int]:
    wanted = [int(item) for item in catalog_ids]
    if not wanted:
        return []
    await init_catalog_table()
    placeholders = ",".join("?" for _ in wanted)
    async with aiosqlite.connect(config.DATABASE_PATH) as conn:
        async with conn.execute(
            f"SELECT id, rebecca_service_id FROM rebecca_services WHERE id IN ({placeholders}) AND is_enabled = 1",
            tuple(wanted),
        ) as cur:
            rows = await cur.fetchall()
    found = {int(row[0]): int(row[1]) for row in rows}
    if set(found) != set(wanted):
        raise ValueError("One or more selected Rebecca catalog services are missing or disabled")
    return [found[item] for item in wanted]


async def discover_services_for_user(username: str) -> dict[str, Any]:
    """Discover the Rebecca service assigned to an exact config/user username.

    Official Rebecca currently exposes one ``service_id``/``service_name`` pair
    per user. The return shape intentionally uses a list so the catalog/UI can
    remain provider-independent if Rebecca later supports multiple assignments.
    """
    requested = str(username or "").strip()
    if not requested:
        raise RebeccaDiscoveryError("Username is required")
    try:
        data = await rebecca_api.discover_services_for_user(requested)
    except RebeccaAPIError as exc:
        if exc.status_code == 404:
            raise RebeccaDiscoveryNotFound("Rebecca user/config was not found") from exc
        raise

    if not isinstance(data, dict):
        raise RebeccaDiscoveryError("Rebecca returned an invalid discovery response")
    returned_username = data.get("username")
    if not isinstance(returned_username, str) or returned_username != requested:
        raise RebeccaDiscoveryError("Rebecca discovery username verification failed")
    service_id = data.get("service_id")
    if isinstance(service_id, bool) or not isinstance(service_id, int) or service_id <= 0:
        raise RebeccaDiscoveryError("Rebecca returned an invalid service_id")
    service_name = data.get("service_name")
    if service_name is not None and not isinstance(service_name, str):
        raise RebeccaDiscoveryError("Rebecca returned an invalid service_name")

    return {
        "username": returned_username,
        "services": [{"service_id": service_id, "service_name": service_name}],
    }


def discovery_confirmation_html(username: str, service_id: int, service_name: str | None) -> str:
    provider = service_name if service_name else "نام ثبت نشده"
    return (
        "🔎 <b>سرویس شناسایی شد</b>\n\n"
        f"👤 کانفیگ: <code>{escape(str(username))}</code>\n"
        f"🆔 شناسه سرویس: <code>{int(service_id)}</code>\n"
        f"🏷 نام Rebecca: {escape(str(provider))}"
    )
