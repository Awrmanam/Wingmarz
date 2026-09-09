from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import aiosqlite

import config
from service_marketplace_service import service_marketplace_service


@dataclass(slots=True)
class ProductCategory:
    id: int
    rebecca_service_id: int
    name: str
    description: str
    is_active: bool
    sort_order: int
    provider_name: str | None = None


class ProductCatalogService:
    """Presentation catalog layered over Rebecca services.

    Rebecca service/inbound names remain provider metadata. Customers see only
    product category names such as WireGuard/OpenVPN and commercial plan data.
    """

    def __init__(self, db_path: str | None = None):
        self._db_path = db_path

    @property
    def db_path(self) -> str:
        return self._db_path or config.DATABASE_PATH

    @staticmethod
    def default_public_name(value: str | None) -> str:
        raw = str(value or "").strip()
        key = "".join(ch for ch in raw.lower() if ch.isalnum())
        if key in {"wire", "wg", "wireguard"}:
            return "WireGuard"
        if key in {"open", "ovpn", "openvpn"}:
            return "OpenVPN"
        return raw or "سرویس"

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
        """Seed missing public categories without overwriting admin edits."""
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            try:
                async with conn.execute(
                    "SELECT rebecca_service_id,display_name,provider_name,is_enabled FROM rebecca_services"
                ) as cur:
                    rows = await cur.fetchall()
            except aiosqlite.OperationalError:
                return
            for row in rows:
                public_name = self.default_public_name(row["display_name"] or row["provider_name"])
                await conn.execute(
                    """
                    INSERT OR IGNORE INTO product_categories(
                        rebecca_service_id,name,is_active
                    ) VALUES(?,?,?)
                    """,
                    (int(row["rebecca_service_id"]), public_name, 1 if row["is_enabled"] else 0),
                )
            await conn.commit()

    async def categories(self, *, active_only: bool = False, sellable_only: bool = False) -> list[ProductCategory]:
        await self.ensure_schema()
        sql = (
            "SELECT pc.*, rs.provider_name FROM product_categories pc "
            "LEFT JOIN rebecca_services rs ON rs.rebecca_service_id=pc.rebecca_service_id"
        )
        params: list[Any] = []
        where: list[str] = []
        if active_only:
            where.append("pc.is_active=1")
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
            if await self.plans_for_category(item.id, only_active=True):
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
        )

    async def get_category(self, category_id: int) -> ProductCategory | None:
        await self.ensure_schema()
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                """
                SELECT pc.*,rs.provider_name FROM product_categories pc
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
                SELECT pc.*,rs.provider_name FROM product_categories pc
                LEFT JOIN rebecca_services rs ON rs.rebecca_service_id=pc.rebecca_service_id
                WHERE pc.rebecca_service_id=?
                """,
                (int(service_id),),
            ) as cur:
                row = await cur.fetchone()
        return self._category(row) if row else None

    async def update_category(self, category_id: int, *, name: str | None = None,
                              description: str | None = None, is_active: bool | None = None) -> bool:
        fields: list[str] = []
        values: list[Any] = []
        if name is not None:
            clean = str(name).strip()
            if not clean or len(clean) > 64:
                raise ValueError("نام دسته باید بین ۱ تا ۶۴ کاراکتر باشد.")
            fields.append("name=?")
            values.append(clean)
        if description is not None:
            clean = str(description).strip()
            if len(clean) > 600:
                raise ValueError("توضیحات دسته حداکثر ۶۰۰ کاراکتر است.")
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
            cur = await conn.execute(
                f"UPDATE product_categories SET {','.join(fields)} WHERE id=?",
                tuple(values),
            )
            await conn.commit()
            return cur.rowcount > 0

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

    async def create_plan(self, *, category_id: int, name: str, description: str,
                          price: int, traffic_bytes: int | None, duration_seconds: int | None,
                          max_users: int | None) -> int:
        category = await self.get_category(category_id)
        if not category:
            raise ValueError("دسته پیدا نشد.")
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
                (clean_name, "both", traffic_bytes, duration_seconds, max_users,
                 int(price), str(category.rebecca_service_id)),
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
