"""Isolated, encrypted inventory and order ledger for the Fastly digital shop.

Nothing in this module reads or writes fixed-plan orders, Rebecca users, PAYG,
wallet balances, or panel provisioning. Manual payment review only.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import time
from typing import Any

import aiosqlite

import config


class FastlyStoreError(ValueError):
    pass


class FastlyStore:
    def __init__(self, db_path: str | None = None):
        self.db_path_override = db_path

    @property
    def db_path(self) -> str:
        return self.db_path_override or config.DATABASE_PATH

    @staticmethod
    def is_enabled() -> bool:
        return os.getenv("FASTLY_STORE_ENABLED", "1").strip().lower() in {"1", "yes", "true", "on"}

    @staticmethod
    def _cipher():
        from cryptography.fernet import Fernet
        key = os.getenv("FASTLY_STORE_FERNET_KEY", "").strip()
        if not key:
            raise FastlyStoreError("کلید رمزگذاری فروشگاه تنظیم نشده است.")
        try:
            return Fernet(key.encode())
        except Exception as exc:
            raise FastlyStoreError("کلید رمزگذاری فروشگاه معتبر نیست.") from exc

    @classmethod
    def _fingerprint(cls, login: str) -> str:
        # The secret key prevents offline guessing of email addresses from the DB.
        key = os.getenv("FASTLY_STORE_FERNET_KEY", "").strip().encode()
        if not key:
            raise FastlyStoreError("کلید رمزگذاری فروشگاه تنظیم نشده است.")
        return hmac.new(key, b"fastly:" + login.lower().strip().encode(), hashlib.sha256).hexdigest()

    async def init(self) -> None:
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.executescript("""
                CREATE TABLE IF NOT EXISTS fastly_products (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
                    price_toman INTEGER NOT NULL CHECK(price_toman > 0),
                    max_quantity INTEGER NOT NULL DEFAULT 10 CHECK(max_quantity BETWEEN 1 AND 100),
                    warranty_days INTEGER NOT NULL DEFAULT 0 CHECK(warranty_days BETWEEN 0 AND 3650),
                    warranty_replacements INTEGER NOT NULL DEFAULT 0 CHECK(warranty_replacements BETWEEN 0 AND 100),
                    rules TEXT NOT NULL DEFAULT '', enabled INTEGER NOT NULL DEFAULT 1,
                    created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS fastly_inventory (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    product_id INTEGER NOT NULL REFERENCES fastly_products(id),
                    fingerprint TEXT NOT NULL UNIQUE,
                    login_secret BLOB NOT NULL, password_secret BLOB NOT NULL,
                    state TEXT NOT NULL DEFAULT 'available'
                      CHECK(state IN ('available','delivered','quarantined')),
                    created_at INTEGER NOT NULL, delivered_at INTEGER,
                    CHECK(length(login_secret) > 0 AND length(password_secret) > 0)
                );
                CREATE INDEX IF NOT EXISTS idx_fastly_stock
                  ON fastly_inventory(product_id,state,id);
                CREATE TABLE IF NOT EXISTS fastly_orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    buyer_id INTEGER NOT NULL CHECK(buyer_id > 0),
                    product_id INTEGER NOT NULL REFERENCES fastly_products(id),
                    quantity INTEGER NOT NULL CHECK(quantity BETWEEN 1 AND 100),
                    title_snapshot TEXT NOT NULL, unit_price_snapshot INTEGER NOT NULL,
                    total_toman INTEGER NOT NULL, warranty_days_snapshot INTEGER NOT NULL,
                    warranty_replacements_snapshot INTEGER NOT NULL,
                    rules_snapshot TEXT NOT NULL, reason_code TEXT NOT NULL DEFAULT 'first',
                    state TEXT NOT NULL DEFAULT 'pending'
                      CHECK(state IN ('pending','submitted','allocated','out_of_stock','rejected')),
                    receipt_file_id TEXT, approved_by INTEGER, reviewed_at INTEGER,
                    revealed_at INTEGER, created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_fastly_orders_buyer
                  ON fastly_orders(buyer_id,id);
                CREATE TABLE IF NOT EXISTS fastly_order_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_id INTEGER NOT NULL REFERENCES fastly_orders(id),
                    inventory_id INTEGER NOT NULL UNIQUE REFERENCES fastly_inventory(id),
                    replaces_item_id INTEGER UNIQUE REFERENCES fastly_order_items(id),
                    assigned_at INTEGER NOT NULL, warranty_expires_at INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS fastly_claims (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    buyer_id INTEGER NOT NULL, order_item_id INTEGER NOT NULL
                      REFERENCES fastly_order_items(id),
                    reason_code TEXT NOT NULL, reason_note TEXT NOT NULL DEFAULT '',
                    state TEXT NOT NULL DEFAULT 'pending'
                      CHECK(state IN ('pending','awaiting_stock','replaced','rejected')),
                    replacement_item_id INTEGER REFERENCES fastly_order_items(id),
                    decided_by INTEGER, decided_at INTEGER, created_at INTEGER NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_fastly_open_claim
                  ON fastly_claims(order_item_id)
                  WHERE state IN ('pending','awaiting_stock');
                CREATE TABLE IF NOT EXISTS fastly_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    actor_id INTEGER NOT NULL, action TEXT NOT NULL,
                    object_type TEXT NOT NULL, object_id INTEGER NOT NULL,
                    created_at INTEGER NOT NULL
                );
            """)
            await conn.commit()

    @staticmethod
    async def _audit(conn, actor: int, action: str, kind: str, ident: int) -> None:
        await conn.execute(
            "INSERT INTO fastly_audit(actor_id,action,object_type,object_id,created_at)"
            " VALUES(?,?,?,?,?)",
            (int(actor), action, kind, int(ident), int(time.time())),
        )

    @staticmethod
    async def _one(conn, sql: str, args: tuple = ()) -> dict | None:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(sql, args) as cur:
            result = await cur.fetchone()
        return dict(result) if result else None

    async def products(self, enabled_only: bool = True) -> list[dict]:
        await self.init()
        where = "WHERE p.enabled=1" if enabled_only else ""
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                "SELECT p.*,COUNT(CASE WHEN i.state='available' THEN 1 END) AS stock "
                "FROM fastly_products p LEFT JOIN fastly_inventory i ON i.product_id=p.id "
                + where + " GROUP BY p.id ORDER BY p.id DESC"
            ) as cur:
                return [dict(x) for x in await cur.fetchall()]

    async def product(self, product_id: int) -> dict | None:
        return next((p for p in await self.products(False) if p["id"] == int(product_id)), None)

    async def add_product(self, title: str, price: int, warranty_days: int,
                          replacements: int, rules: str, actor: int) -> int:
        title, rules = str(title).strip(), str(rules).strip()
        if not 2 <= len(title) <= 100 or not 1 <= int(price) <= 2_000_000_000:
            raise FastlyStoreError("عنوان یا قیمت نامعتبر است.")
        if not 0 <= int(warranty_days) <= 3650 or not 0 <= int(replacements) <= 100:
            raise FastlyStoreError("مقدار ضمانت نامعتبر است.")
        if not rules or len(rules) > 3500:
            raise FastlyStoreError("قوانین باید بین ۱ تا ۳۵۰۰ نویسه باشند.")
        await self.init()
        now = int(time.time())
        async with aiosqlite.connect(self.db_path) as conn:
            cur = await conn.execute(
                "INSERT INTO fastly_products(title,price_toman,warranty_days,warranty_replacements,rules,created_at,updated_at)"
                " VALUES(?,?,?,?,?,?,?)",
                (title, int(price), int(warranty_days), int(replacements), rules, now, now),
            )
            ident = int(cur.lastrowid)
            await self._audit(conn, actor, "product.create", "product", ident)
            await conn.commit()
            return ident

    async def edit_product(self, product_id: int, field: str, value: Any, actor: int) -> bool:
        allowed = {"title", "price_toman", "rules", "enabled", "warranty_days",
                   "warranty_replacements", "description", "max_quantity"}
        if field not in allowed:
            raise FastlyStoreError("فیلد نامعتبر است.")
        if field in {"price_toman","warranty_days","warranty_replacements","enabled","max_quantity"}:
            value = int(value)
            limits = {"price_toman": (1, 2_000_000_000), "warranty_days": (0,3650),
                      "warranty_replacements": (0,100), "enabled": (0,1), "max_quantity": (1,100)}
            lo, hi = limits[field]
            if not lo <= value <= hi:
                raise FastlyStoreError("مقدار نامعتبر است.")
        else:
            value = str(value).strip()
            if not value or len(value) > (3500 if field == "rules" else 200):
                raise FastlyStoreError("متن نامعتبر است.")
        await self.init()
        async with aiosqlite.connect(self.db_path) as conn:
            cur = await conn.execute(
                f"UPDATE fastly_products SET {field}=?,updated_at=? WHERE id=?",
                (value, int(time.time()), int(product_id)),
            )
            if cur.rowcount:
                await self._audit(conn, actor, "product.edit."+field, "product", product_id)
            await conn.commit()
            return bool(cur.rowcount)

    async def add_stock(self, product_id: int, pairs: list[tuple[str,str]], actor: int) -> tuple[int,int]:
        product = await self.product(product_id)
        if not product:
            raise FastlyStoreError("محصول پیدا نشد.")
        if not 1 <= len(pairs) <= 200:
            raise FastlyStoreError("در هر مرحله حداکثر ۲۰۰ اکانت وارد کنید.")
        cipher = self._cipher()
        now = int(time.time())
        added, skipped = 0, 0
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute("BEGIN IMMEDIATE")
            for login, password in pairs:
                login, password = str(login).strip(), str(password).strip()
                if not 3 <= len(login) <= 254 or not 1 <= len(password) <= 1024 or any(
                    c in login + password for c in "\r\n\x00"
                ):
                    skipped += 1
                    continue
                fp = self._fingerprint(login)
                cur = await conn.execute(
                    "INSERT OR IGNORE INTO fastly_inventory"
                    "(product_id,fingerprint,login_secret,password_secret,created_at)"
                    " VALUES(?,?,?,?,?)",
                    (int(product_id), fp, cipher.encrypt(login.encode()),
                     cipher.encrypt(password.encode()), now),
                )
                added += int(cur.rowcount == 1)
                skipped += int(cur.rowcount == 0)
            await self._audit(conn, actor, f"stock.import.{added}", "product", product_id)
            await conn.commit()
        return added, skipped

    async def make_order(self, buyer: int, product_id: int, qty: int, reason: str) -> int:
        product = await self.product(product_id)
        if not product or not product["enabled"]:
            raise FastlyStoreError("این محصول در حال حاضر قابل سفارش نیست.")
        if not 1 <= qty <= int(product["max_quantity"]):
            raise FastlyStoreError("تعداد درخواستی خارج از محدوده است.")
        if qty > int(product["stock"]):
            raise FastlyStoreError("موجودی برای این تعداد کافی نیست.")
        await self.init()
        now = int(time.time())
        reason = reason if reason in {"first","extra","blocked","login","quality","other"} else "other"
        async with aiosqlite.connect(self.db_path) as conn:
            cur = await conn.execute(
                "INSERT INTO fastly_orders(buyer_id,product_id,quantity,title_snapshot,"
                "unit_price_snapshot,total_toman,warranty_days_snapshot,"
                "warranty_replacements_snapshot,rules_snapshot,reason_code,created_at,updated_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (int(buyer), int(product_id), qty, product["title"], product["price_toman"],
                 qty*int(product["price_toman"]), product["warranty_days"],
                 product["warranty_replacements"], product["rules"], reason, now, now),
            )
            ident = int(cur.lastrowid)
            await self._audit(conn, buyer, "order.create", "order", ident)
            await conn.commit()
            return ident

    async def order(self, order_id: int, buyer: int | None = None) -> dict | None:
        await self.init()
        async with aiosqlite.connect(self.db_path) as conn:
            item = await self._one(conn, "SELECT * FROM fastly_orders WHERE id=?", (int(order_id),))
        return item if item and (buyer is None or item["buyer_id"] == int(buyer)) else None

    async def orders(self, buyer: int | None = None, state: str | None = None, limit: int = 30) -> list[dict]:
        await self.init()
        clauses, args = [], []
        if buyer is not None:
            clauses.append("buyer_id=?"); args.append(int(buyer))
        if state is not None:
            clauses.append("state=?"); args.append(state)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                "SELECT id,buyer_id,title_snapshot,quantity,total_toman,reason_code,state,created_at "
                "FROM fastly_orders" + where + " ORDER BY id DESC LIMIT ?",
                (*args, max(1,min(int(limit),100))),
            ) as cur:
                return [dict(x) for x in await cur.fetchall()]

    async def has_purchases(self, buyer: int) -> bool:
        await self.init()
        async with aiosqlite.connect(self.db_path) as conn:
            return bool(await self._one(
                conn, "SELECT id FROM fastly_orders WHERE buyer_id=? AND state='allocated' LIMIT 1",
                (int(buyer),)
            ))

    async def submit_receipt(self, order_id: int, buyer: int, file_id: str) -> bool:
        await self.init()
        async with aiosqlite.connect(self.db_path) as conn:
            cur = await conn.execute(
                "UPDATE fastly_orders SET state='submitted',receipt_file_id=?,updated_at=? "
                "WHERE id=? AND buyer_id=? AND state='pending'",
                (str(file_id), int(time.time()), int(order_id), int(buyer)),
            )
            if cur.rowcount:
                await self._audit(conn, buyer, "payment.receipt", "order", order_id)
            await conn.commit()
            return bool(cur.rowcount)

    async def approve(self, order_id: int, actor: int) -> str:
        """Exactly-once atomic allocation. Staff must independently confirm bank transfer."""
        await self.init()
        now = int(time.time())
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute("BEGIN IMMEDIATE")
            order = await self._one(conn, "SELECT * FROM fastly_orders WHERE id=?", (int(order_id),))
            if not order:
                raise FastlyStoreError("سفارش وجود ندارد.")
            if order["state"] == "allocated":
                await conn.rollback(); return "already_allocated"
            if order["state"] == "out_of_stock":
                await conn.rollback(); return "out_of_stock"
            if order["state"] != "submitted":
                raise FastlyStoreError("سفارش آماده تأیید نیست.")
            async with conn.execute(
                "SELECT id FROM fastly_inventory WHERE product_id=? AND state='available' "
                "ORDER BY id LIMIT ?",
                (order["product_id"], order["quantity"]),
            ) as cur:
                ids = [int(x[0]) for x in await cur.fetchall()]
            if len(ids) != int(order["quantity"]):
                await conn.execute(
                    "UPDATE fastly_orders SET state='out_of_stock',approved_by=?,reviewed_at=?,updated_at=? WHERE id=?",
                    (int(actor), now, now, int(order_id)),
                )
                await self._audit(conn, actor, "payment.confirmed.stock_shortage", "order", order_id)
                await conn.commit()
                return "out_of_stock"
            expires = now + int(order["warranty_days_snapshot"]) * 86400 if order["warranty_days_snapshot"] else 0
            for inv in ids:
                cur = await conn.execute(
                    "UPDATE fastly_inventory SET state='delivered',delivered_at=? "
                    "WHERE id=? AND state='available'",
                    (now, inv),
                )
                if cur.rowcount != 1:
                    raise FastlyStoreError("رقابت در اختصاص موجودی؛ دوباره تلاش کنید.")
                await conn.execute(
                    "INSERT INTO fastly_order_items(order_id,inventory_id,assigned_at,warranty_expires_at)"
                    " VALUES(?,?,?,?)", (int(order_id), inv, now, expires),
                )
            await conn.execute(
                "UPDATE fastly_orders SET state='allocated',approved_by=?,reviewed_at=?,updated_at=? WHERE id=?",
                (int(actor), now, now, int(order_id)),
            )
            await self._audit(conn, actor, "order.allocate", "order", order_id)
            await conn.commit()
        return "allocated"

    async def reject(self, order_id: int, actor: int) -> bool:
        await self.init()
        async with aiosqlite.connect(self.db_path) as conn:
            cur = await conn.execute(
                "UPDATE fastly_orders SET state='rejected',approved_by=?,reviewed_at=?,updated_at=? "
                "WHERE id=? AND state='submitted'",
                (int(actor), int(time.time()), int(time.time()), int(order_id)),
            )
            if cur.rowcount:
                await self._audit(conn, actor, "payment.rejected", "order", order_id)
            await conn.commit()
            return bool(cur.rowcount)

    async def reveal(self, order_id: int, buyer: int) -> list[dict]:
        """Only the buyer can decrypt, only after allocation and approval."""
        order = await self.order(order_id, buyer=buyer)
        if not order or order["state"] != "allocated":
            raise FastlyStoreError("دسترسی به این سفارش مجاز نیست.")
        cipher = self._cipher()
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                "SELECT i.id,i.login_secret,i.password_secret "
                "FROM fastly_order_items oi JOIN fastly_inventory i ON i.id=oi.inventory_id "
                "WHERE oi.order_id=? AND NOT EXISTS"
                "(SELECT 1 FROM fastly_order_items r WHERE r.replaces_item_id=oi.id) "
                "ORDER BY oi.id",
                (int(order_id),),
            ) as cur:
                items = [dict(x) for x in await cur.fetchall()]
        if len(items) != order["quantity"]:
            raise FastlyStoreError("لیست اکانت‌های سفارش ناقص است.")
        return [{
            "id": x["id"], "login": cipher.decrypt(x["login_secret"]).decode(),
            "password": cipher.decrypt(x["password_secret"]).decode()
        } for x in items]

    async def mark_revealed(self, order_id: int, buyer: int) -> None:
        await self.init()
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                "UPDATE fastly_orders SET revealed_at=COALESCE(revealed_at,?),updated_at=? "
                "WHERE id=? AND buyer_id=? AND state='allocated'",
                (int(time.time()), int(time.time()), int(order_id), int(buyer)),
            )
            await conn.commit()

    async def claimable_items(self, buyer: int) -> list[dict]:
        await self.init()
        now = int(time.time())
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                """SELECT oi.id,oi.order_id,o.title_snapshot,oi.warranty_expires_at
                   FROM fastly_order_items oi JOIN fastly_orders o ON o.id=oi.order_id
                   WHERE o.buyer_id=? AND o.state='allocated'
                   AND oi.warranty_expires_at>? AND o.warranty_replacements_snapshot>0
                   AND NOT EXISTS(SELECT 1 FROM fastly_order_items new WHERE new.replaces_item_id=oi.id)
                   ORDER BY oi.id DESC LIMIT 40""", (int(buyer), now),
            ) as cur:
                return [dict(row) for row in await cur.fetchall()]

    async def claim(self, buyer: int, item_id: int, reason: str, note: str = "") -> int:
        if reason not in {"blocked","login","quality","other"} or len(note) > 500:
            raise FastlyStoreError("دلیل درخواست معتبر نیست.")
        await self.init()
        now = int(time.time())
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute("BEGIN IMMEDIATE")
            item = await self._one(conn,
                """SELECT oi.*,o.buyer_id,o.warranty_replacements_snapshot FROM fastly_order_items oi
                   JOIN fastly_orders o ON o.id=oi.order_id WHERE oi.id=?""",
                (int(item_id),))
            if not item or item["buyer_id"] != int(buyer) or item["warranty_expires_at"] <= now:
                raise FastlyStoreError("این اکانت مشمول درخواست تعویض نیست.")
            if await self._one(conn,"SELECT id FROM fastly_order_items WHERE replaces_item_id=?", (int(item_id),)):
                raise FastlyStoreError("این اکانت قبلاً تعویض شده است.")
            count = 0; cursor = item
            while cursor["replaces_item_id"]:
                count += 1
                cursor = await self._one(conn, "SELECT * FROM fastly_order_items WHERE id=?", (cursor["replaces_item_id"],))
            if count >= item["warranty_replacements_snapshot"]:
                raise FastlyStoreError("سقف تعداد تعویض این سفارش تمام شده است.")
            if await self._one(conn,
                "SELECT id FROM fastly_claims WHERE order_item_id=? AND state IN ('pending','awaiting_stock')",
                (int(item_id),)):
                raise FastlyStoreError("درخواست قبلی در حال بررسی است.")
            cur = await conn.execute(
                "INSERT INTO fastly_claims(buyer_id,order_item_id,reason_code,reason_note,created_at)"
                " VALUES(?,?,?,?,?)",
                (int(buyer), int(item_id), reason, note, now),
            )
            ident = int(cur.lastrowid)
            await self._audit(conn, buyer, "warranty.request", "claim", ident)
            await conn.commit()
            return ident

    async def claims(self, state: str = "pending") -> list[dict]:
        await self.init()
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                """SELECT c.id,c.buyer_id,c.order_item_id,c.reason_code,c.reason_note,c.state,
                   o.title_snapshot,o.id AS order_id
                   FROM fastly_claims c JOIN fastly_order_items oi ON oi.id=c.order_item_id
                   JOIN fastly_orders o ON o.id=oi.order_id
                   WHERE c.state=? ORDER BY c.id DESC LIMIT 100""", (state,),
            ) as cur:
                return [dict(x) for x in await cur.fetchall()]

    async def decide_claim(self, claim_id: int, actor: int, approve: bool) -> str:
        """Allocate a new unit only after an explicit staff decision."""
        await self.init()
        now = int(time.time())
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute("BEGIN IMMEDIATE")
            item = await self._one(conn,
                """SELECT c.*,oi.order_id,oi.warranty_expires_at,o.product_id
                   FROM fastly_claims c JOIN fastly_order_items oi ON oi.id=c.order_item_id
                   JOIN fastly_orders o ON o.id=oi.order_id WHERE c.id=?""",
                (int(claim_id),))
            if not item:
                raise FastlyStoreError("درخواست پیدا نشد.")
            if item["state"] not in {"pending","awaiting_stock"}:
                await conn.rollback(); return item["state"]
            if not approve:
                await conn.execute(
                    "UPDATE fastly_claims SET state='rejected',decided_by=?,decided_at=? WHERE id=?",
                    (int(actor), now, int(claim_id)),
                )
                result = "rejected"
            else:
                if item["warranty_expires_at"] <= now:
                    raise FastlyStoreError("اعتبار ضمانت پایان یافته است.")
                inv = await self._one(conn,
                    "SELECT id FROM fastly_inventory WHERE product_id=? AND state='available' ORDER BY id LIMIT 1",
                    (item["product_id"],))
                if not inv:
                    await conn.execute("UPDATE fastly_claims SET state='awaiting_stock' WHERE id=?",(int(claim_id),))
                    result = "awaiting_stock"
                else:
                    await conn.execute(
                        "UPDATE fastly_inventory SET state='delivered',delivered_at=? WHERE id=?",
                        (now, inv["id"]),
                    )
                    cur = await conn.execute(
                        "INSERT INTO fastly_order_items(order_id,inventory_id,replaces_item_id,assigned_at,warranty_expires_at)"
                        " VALUES(?,?,?,?,?)",
                        (item["order_id"], inv["id"], item["order_item_id"], now, item["warranty_expires_at"]),
                    )
                    await conn.execute(
                        "UPDATE fastly_claims SET state='replaced',replacement_item_id=?,decided_by=?,decided_at=? WHERE id=?",
                        (int(cur.lastrowid), int(actor), now, int(claim_id)),
                    )
                    result = "replaced"
            await self._audit(conn, actor, "warranty."+result, "claim", claim_id)
            await conn.commit()
            return result

    async def stats(self) -> dict:
        await self.init()
        async with aiosqlite.connect(self.db_path) as conn:
            result = {}
            for key, query in {
                "available": "SELECT count(*) FROM fastly_inventory WHERE state='available'",
                "delivered": "SELECT count(*) FROM fastly_inventory WHERE state='delivered'",
                "orders": "SELECT count(*) FROM fastly_orders",
                "paid_orders": "SELECT count(*) FROM fastly_orders WHERE state='allocated'",
                "pending": "SELECT count(*) FROM fastly_orders WHERE state='submitted'",
                "sold_units": "SELECT COALESCE(sum(quantity),0) FROM fastly_orders WHERE state='allocated'",
                "replacements": "SELECT count(*) FROM fastly_claims WHERE state='replaced'",
                "claims_pending": "SELECT count(*) FROM fastly_claims WHERE state IN ('pending','awaiting_stock')",
                "revenue_toman": "SELECT COALESCE(sum(total_toman),0) FROM fastly_orders WHERE state IN ('allocated','out_of_stock')",
            }.items():
                async with conn.execute(query) as cur:
                    result[key] = int((await cur.fetchone())[0] or 0)
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                "SELECT reason_code,count(*) AS count FROM fastly_orders GROUP BY reason_code ORDER BY count DESC"
            ) as cur:
                result["reasons"] = [dict(x) for x in await cur.fetchall()]
            return result


fastly_store = FastlyStore()
