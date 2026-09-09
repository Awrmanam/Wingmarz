from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

import aiosqlite

import config
from service_marketplace_service import service_marketplace_service
from style_engine import style_engine


@dataclass(slots=True)
class ProductCategory:
    id: int
    rebecca_service_id: int
    name: str
    description: str
    is_active: bool
    sort_order: int
    provider_name: str | None = None
    provider_enabled: bool = True


class ProductCatalogService:
    """Customer-facing panel catalog layered over Rebecca service mappings.

    ``rebecca_services.display_name`` is the canonical panel name chosen by the
    bot owner. Rebecca's provider/inbound name and numeric service id remain
    internal routing metadata. Products, trials and renewals should resolve their
    visible panel identity through this service instead of reading provider names.
    """

    def __init__(self, db_path: str | None = None):
        self._db_path = db_path

    @property
    def db_path(self) -> str:
        return self._db_path or config.DATABASE_PATH

    @staticmethod
    def default_public_name(value: str | None) -> str:
        """Fallback only for old/incomplete records.

        New Rebecca records already have an owner-chosen ``display_name`` and that
        value must be preserved verbatim rather than silently renamed by the bot.
        """
        raw = str(value or "").strip()
        return raw or "پنل"

    @staticmethod
    def _emoji_key_candidates(*values: str | None) -> list[str]:
        candidates: list[str] = []
        aliases = {
            "wireguard": "wire",
            "wg": "wire",
            "openvpn": "openvpn",
            "ovpn": "openvpn",
        }
        for value in values:
            raw = str(value or "").strip().lower()
            if not raw:
                continue
            compact = re.sub(r"[^a-z0-9_.-]+", "", raw)
            if compact and compact not in candidates:
                candidates.append(compact)
            alias = aliases.get(compact)
            if alias and alias not in candidates:
                candidates.append(alias)
        return candidates

    async def resolve_panel_icon_key(self, category: ProductCategory) -> str | None:
        """Reuse an already registered Premium Emoji when its key matches panel identity.

        This intentionally does not invent an emoji id. For example an owner who
        registered ``wire`` once will automatically get that same Premium Emoji on
        a Wire/WireGuard panel button everywhere the central panel identity is used.
        """
        for key in self._emoji_key_candidates(category.name, category.provider_name):
            if await style_engine.get_emoji(key):
                return key
        return None

    async def ensure_schema(self) -> None:
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS product_categories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    rebecca_service_id INTEGER NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    is_active INTEGER NOT NULL DEFAULT 1,
                    sort_order INTEGER NOT NULL DEFAULT 100,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS plan_presentation (
                    plan_id INTEGER PRIMARY KEY,
                    description TEXT NOT NULL DEFAULT '',
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
            await conn.commit()
        await self.sync_from_rebecca()

    async def sync_from_rebecca(self) -> None:
        """Synchronize panel identity from the Rebecca catalog without exposing provider data.

        The user-facing name has one source of truth: ``rebecca_services.display_name``.
        Existing product-category rows are therefore kept aligned with that name. A
        category whose Rebecca mapping was removed is archived instead of deleted so
        historical plans/orders stay intact.
        """
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            try:
                async with conn.execute(
                    "SELECT rebecca_service_id,display_name,provider_name,is_enabled FROM rebecca_services"
                ) as cur:
                    rows = await cur.fetchall()
            except aiosqlite.OperationalError:
                return

            active_service_ids: list[int] = []
            for row in rows:
                service_id = int(row["rebecca_service_id"])
                active_service_ids.append(service_id)
                panel_name = self.default_public_name(row["display_name"] or row["provider_name"])
                await conn.execute(
                    """
                    INSERT INTO product_categories(rebecca_service_id,name,is_active)
                    VALUES(?,?,?)
                    ON CONFLICT(rebecca_service_id) DO UPDATE SET
                        name=excluded.name,
                        updated_at=CASE
                            WHEN product_categories.name<>excluded.name THEN CURRENT_TIMESTAMP
                            ELSE product_categories.updated_at
                        END
                    """,
                    (service_id, panel_name, 1 if row["is_enabled"] else 0),
                )

            # Safe orphan handling: never destroy presentation/history automatically.
            if active_service_ids:
                placeholders = ",".join("?" for _ in active_service_ids)
                await conn.execute(
                    f"UPDATE product_categories SET is_active=0 WHERE rebecca_service_id NOT IN ({placeholders})",
                    tuple(active_service_ids),
                )
            else:
                await conn.execute("UPDATE product_categories SET is_active=0")
            await conn.commit()

    async def categories(self, *, active_only: bool = False, sellable_only: bool = False) -> list[ProductCategory]:
        await self.ensure_schema()
        sql = (
            "SELECT pc.*, rs.provider_name, COALESCE(rs.is_enabled,0) AS provider_enabled "
            "FROM product_categories pc "
            "LEFT JOIN rebecca_services rs ON rs.rebecca_service_id=pc.rebecca_service_id"
        )
        params: list[Any] = []
        where: list[str] = []
        if active_only:
            where.extend(["pc.is_active=1", "COALESCE(rs.is_enabled,0)=1"])
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY pc.sort_order, pc.name COLLATE NOCASE, pc.id"
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(sql, tuple(params)) as cur:
                rows = await cur.fetchall()
        items = [self._category(row) for row in rows]
        if not sellable_only:
            return items
        result: list[ProductCategory] = []
        for item in items:
            if item.provider_enabled and await self.plans_for_category(item.id, only_active=True):
                result.append(item)
        return result

    @staticmethod
    def _category(row: aiosqlite.Row) -> ProductCategory:
        keys = set(row.keys())
        return ProductCategory(
            id=int(row["id"]),
            rebecca_service_id=int(row["rebecca_service_id"]),
            name=str(row["name"]),
            description=str(row["description"] or ""),
            is_active=bool(row["is_active"]),
            sort_order=int(row["sort_order"] or 100),
            provider_name=row["provider_name"] if "provider_name" in keys else None,
            provider_enabled=bool(row["provider_enabled"]) if "provider_enabled" in keys else True,
        )

    async def get_category(self, category_id: int) -> ProductCategory | None:
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                """
                SELECT pc.*,rs.provider_name,COALESCE(rs.is_enabled,0) AS provider_enabled
                FROM product_categories pc
                LEFT JOIN rebecca_services rs ON rs.rebecca_service_id=pc.rebecca_service_id
                WHERE pc.id=?
                """,
                (int(category_id),),
            ) as cur:
                row = await cur.fetchone()
        return self._category(row) if row else None

    async def get_category_by_service(self, service_id: int) -> ProductCategory | None:
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                """
                SELECT pc.*,rs.provider_name,COALESCE(rs.is_enabled,0) AS provider_enabled
                FROM product_categories pc
                LEFT JOIN rebecca_services rs ON rs.rebecca_service_id=pc.rebecca_service_id
                WHERE pc.rebecca_service_id=?
                """,
                (int(service_id),),
            ) as cur:
                row = await cur.fetchone()
        return self._category(row) if row else None

    async def category_for_plan(self, plan_id: int, *, active_only: bool = False) -> ProductCategory | None:
        from database import db

        plan = await db.get_plan_by_id(int(plan_id))
        if not plan:
            return None
        service_ids = service_marketplace_service.plan_service_ids(plan)
        if not service_ids:
            return None
        categories = await self.categories(active_only=active_only)
        return next((item for item in categories if item.rebecca_service_id in service_ids), None)

    async def update_category(
        self,
        category_id: int,
        *,
        name: str | None = None,
        description: str | None = None,
        is_active: bool | None = None,
    ) -> bool:
        fields: list[str] = []
        values: list[Any] = []
        clean_name: str | None = None
        if name is not None:
            clean_name = str(name).strip()
            if not clean_name or len(clean_name) > 64:
                raise ValueError("نام پنل باید بین ۱ تا ۶۴ کاراکتر باشد.")
            fields.append("name=?")
            values.append(clean_name)
        if description is not None:
            clean = str(description).strip()
            if len(clean) > 600:
                raise ValueError("توضیحات پنل حداکثر ۶۰۰ کاراکتر است.")
            fields.append("description=?")
            values.append(clean)
        if is_active is not None:
            fields.append("is_active=?")
            values.append(1 if is_active else 0)
        if not fields:
            return False
        fields.append("updated_at=CURRENT_TIMESTAMP")
        values.append(int(category_id))

        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            await conn.execute("BEGIN IMMEDIATE")
            try:
                async with conn.execute(
                    "SELECT rebecca_service_id FROM product_categories WHERE id=?",
                    (int(category_id),),
                ) as cur:
                    row = await cur.fetchone()
                if not row:
                    await conn.rollback()
                    return False
                cur = await conn.execute(
                    f"UPDATE product_categories SET {','.join(fields)} WHERE id=?",
                    tuple(values),
                )
                if clean_name is not None:
                    await conn.execute(
                        """
                        UPDATE rebecca_services
                        SET display_name=?, updated_at=CURRENT_TIMESTAMP
                        WHERE rebecca_service_id=?
                        """,
                        (clean_name, int(row["rebecca_service_id"])),
                    )
                await conn.commit()
                return cur.rowcount > 0
            except Exception:
                await conn.rollback()
                raise

    async def plans_for_category(self, category_id: int, *, only_active: bool = True) -> list[Any]:
        category = await self.get_category(int(category_id))
        if not category:
            return []
        plans = await self._plans(only_active=only_active)
        return [
            plan for plan in plans
            if category.rebecca_service_id in service_marketplace_service.plan_service_ids(plan)
        ]

    async def _plans(self, *, only_active: bool) -> list[Any]:
        # Use the existing model loader so all legacy purchase/provisioning code
        # continues to receive the same PlanModel objects.
        from database import db
        return await db.get_plans(only_active=only_active)

    async def plan_description(self, plan_id: int) -> str:
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            async with conn.execute("SELECT description FROM plan_presentation WHERE plan_id=?", (int(plan_id),)) as cur:
                row = await cur.fetchone()
        return str(row[0] or "") if row else ""

    async def set_plan_description(self, plan_id: int, description: str) -> None:
        clean = str(description or "").strip()
        if len(clean) > 800:
            raise ValueError("توضیحات پلن حداکثر ۸۰۰ کاراکتر است.")
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                """
                INSERT INTO plan_presentation(plan_id,description,updated_at)
                VALUES(?,?,CURRENT_TIMESTAMP)
                ON CONFLICT(plan_id) DO UPDATE SET
                    description=excluded.description,updated_at=CURRENT_TIMESTAMP
                """,
                (int(plan_id), clean),
            )
            await conn.commit()

    async def update_plan_field(self, plan_id: int, field: str, value: Any) -> bool:
        allowed = {"name", "price", "traffic_limit_bytes", "time_limit_seconds", "max_users", "is_active"}
        if field not in allowed:
            raise ValueError("فیلد پلن قابل ویرایش نیست.")
        async with aiosqlite.connect(self.db_path) as conn:
            cur = await conn.execute(f"UPDATE plans SET {field}=? WHERE id=?", (value, int(plan_id)))
            await conn.commit()
            return cur.rowcount > 0

    async def create_plan(
        self,
        *,
        category_id: int,
        name: str,
        description: str,
        price: int,
        traffic_bytes: int | None,
        duration_seconds: int | None,
        max_users: int | None,
    ) -> int:
        category = await self.get_category(category_id)
        if not category:
            raise ValueError("پنل پیدا نشد.")
        clean_name = str(name).strip()
        if not clean_name or len(clean_name) > 80:
            raise ValueError("نام پلن نامعتبر است.")
        async with aiosqlite.connect(self.db_path) as conn:
            cur = await conn.execute(
                """
                INSERT INTO plans(
                    name,plan_type,traffic_limit_bytes,time_limit_seconds,max_users,
                    price,is_active,allow_incremental_renewal,rebecca_service_ids
                ) VALUES(?,?,?,?,?,?,1,1,?)
                """,
                (
                    clean_name,
                    "both",
                    traffic_bytes,
                    duration_seconds,
                    max_users,
                    int(price),
                    str(category.rebecca_service_id),
                ),
            )
            plan_id = int(cur.lastrowid)
            await conn.execute(
                "INSERT INTO plan_presentation(plan_id,description) VALUES(?,?)",
                (plan_id, str(description or "").strip()),
            )
            await conn.commit()
        return plan_id

    async def safe_delete_plan(self, plan_id: int) -> str:
        """Delete unused plans; soft-delete plans referenced by history/customers."""
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            async with conn.execute("SELECT COUNT(*) FROM orders WHERE plan_id=?", (int(plan_id),)) as cur:
                order_refs = int((await cur.fetchone())[0] or 0)
            async with conn.execute("SELECT COUNT(*) FROM admins WHERE origin_plan_id=?", (int(plan_id),)) as cur:
                admin_refs = int((await cur.fetchone())[0] or 0)
            if order_refs or admin_refs:
                cur = await conn.execute("UPDATE plans SET is_active=0 WHERE id=?", (int(plan_id),))
                await conn.commit()
                return "archived" if cur.rowcount else "missing"
            cur = await conn.execute("DELETE FROM plans WHERE id=?", (int(plan_id),))
            await conn.execute("DELETE FROM plan_presentation WHERE plan_id=?", (int(plan_id),))
            await conn.commit()
            return "deleted" if cur.rowcount else "missing"


product_catalog = ProductCatalogService()
