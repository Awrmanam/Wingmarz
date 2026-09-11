"""Ticket history with atomic replies and explicit, irreversible closure."""
from __future__ import annotations

import math
from typing import Any

import aiosqlite
import config


async def ensure_schema():
    async with aiosqlite.connect(config.DATABASE_PATH) as conn:
        await conn.executescript('''
            CREATE TABLE IF NOT EXISTS support_tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
                subject TEXT NOT NULL, body TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open',
                admin_reply TEXT, replied_by INTEGER, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS support_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT, ticket_id INTEGER NOT NULL,
                author_id INTEGER NOT NULL, body TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
        ''')
        async with conn.execute("PRAGMA table_info(support_tickets)") as cur:
            columns = {str(row[1]) for row in await cur.fetchall()}
        if "closed_by" not in columns:
            await conn.execute("ALTER TABLE support_tickets ADD COLUMN closed_by INTEGER")
        if "closed_at" not in columns:
            await conn.execute("ALTER TABLE support_tickets ADD COLUMN closed_at TIMESTAMP")
        # Preserve replies written by the previous single-reply implementation.
        await conn.execute('''INSERT INTO support_messages(ticket_id,author_id,body)
            SELECT t.id,COALESCE(t.replied_by,0),t.admin_reply FROM support_tickets t
            WHERE t.admin_reply IS NOT NULL AND NOT EXISTS
            (SELECT 1 FROM support_messages m WHERE m.ticket_id=t.id)''')
        await conn.commit()


async def create_ticket(user_id: int, subject: str, body: str) -> int:
    await ensure_schema()
    async with aiosqlite.connect(config.DATABASE_PATH) as conn:
        cur = await conn.execute(
            "INSERT INTO support_tickets(user_id,subject,body) VALUES(?,?,?)",
            (int(user_id), str(subject), str(body)),
        )
        await conn.commit()
        return int(cur.lastrowid)


async def get_ticket(ticket_id: int, *, user_id: int | None = None) -> dict[str, Any] | None:
    await ensure_schema()
    async with aiosqlite.connect(config.DATABASE_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        sql = "SELECT * FROM support_tickets WHERE id=?"
        params: tuple[Any, ...] = (int(ticket_id),)
        if user_id is not None:
            sql += " AND user_id=?"
            params = (int(ticket_id), int(user_id))
        async with conn.execute(sql, params) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None


async def list_user_tickets(
    user_id: int,
    *,
    status: str = "open",
    page: int = 0,
    page_size: int = 8,
) -> tuple[list[dict[str, Any]], int, int, int]:
    await ensure_schema()
    status = "closed" if status == "closed" else "open"
    page = max(0, int(page))
    page_size = max(1, min(20, int(page_size)))
    async with aiosqlite.connect(config.DATABASE_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            "SELECT COUNT(*) FROM support_tickets WHERE user_id=? AND status=?",
            (int(user_id), status),
        ) as cur:
            total = int((await cur.fetchone())[0] or 0)
        pages = max(1, math.ceil(total / page_size))
        page = min(page, pages - 1)
        async with conn.execute(
            """
            SELECT id,user_id,subject,status,created_at,updated_at,closed_at
            FROM support_tickets
            WHERE user_id=? AND status=?
            ORDER BY id DESC
            LIMIT ? OFFSET ?
            """,
            (int(user_id), status, page_size, page * page_size),
        ) as cur:
            rows = [dict(row) for row in await cur.fetchall()]
    return rows, total, page, pages


async def reply_ticket(ticket_id, actor_id, body):
    await ensure_schema()
    async with aiosqlite.connect(config.DATABASE_PATH) as conn:
        await conn.execute('BEGIN IMMEDIATE')
        async with conn.execute("SELECT user_id FROM support_tickets WHERE id=? AND status='open'", (ticket_id,)) as cur:
            row = await cur.fetchone()
        if not row:
            await conn.rollback()
            return None
        await conn.execute('INSERT INTO support_messages(ticket_id,author_id,body) VALUES(?,?,?)', (ticket_id,actor_id,body))
        await conn.execute('UPDATE support_tickets SET admin_reply=?,replied_by=?,updated_at=CURRENT_TIMESTAMP WHERE id=?', (body,actor_id,ticket_id))
        await conn.commit()
        return int(row[0])


async def close_ticket(
    ticket_id: int,
    actor_id: int,
    *,
    expected_user_id: int | None = None,
) -> dict[str, Any] | None:
    """Close an open ticket exactly once; there is intentionally no reopen API."""
    await ensure_schema()
    async with aiosqlite.connect(config.DATABASE_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute("BEGIN IMMEDIATE")
        sql = "SELECT * FROM support_tickets WHERE id=? AND status='open'"
        params: tuple[Any, ...] = (int(ticket_id),)
        if expected_user_id is not None:
            sql += " AND user_id=?"
            params = (int(ticket_id), int(expected_user_id))
        async with conn.execute(sql, params) as cur:
            row = await cur.fetchone()
        if not row:
            await conn.rollback()
            return None
        await conn.execute(
            """
            UPDATE support_tickets
            SET status='closed',closed_by=?,closed_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP
            WHERE id=? AND status='open'
            """,
            (int(actor_id), int(ticket_id)),
        )
        await conn.commit()
        result = dict(row)
        result["status"] = "closed"
        result["closed_by"] = int(actor_id)
        return result


async def history(ticket_id, limit=5):
    await ensure_schema()
    async with aiosqlite.connect(config.DATABASE_PATH) as conn:
        async with conn.execute('SELECT body FROM support_messages WHERE ticket_id=? ORDER BY id DESC LIMIT ?', (ticket_id,limit)) as cur:
            return [row[0] for row in reversed(await cur.fetchall())]
