"""Ticket history with atomic replies and explicit closure."""
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
        # Preserve replies written by the previous single-reply implementation.
        await conn.execute('''INSERT INTO support_messages(ticket_id,author_id,body)
            SELECT t.id,COALESCE(t.replied_by,0),t.admin_reply FROM support_tickets t
            WHERE t.admin_reply IS NOT NULL AND NOT EXISTS
            (SELECT 1 FROM support_messages m WHERE m.ticket_id=t.id)''')
        await conn.commit()


async def reply_ticket(ticket_id, actor_id, body):
    await ensure_schema()
    async with aiosqlite.connect(config.DATABASE_PATH) as conn:
        await conn.execute('BEGIN IMMEDIATE')
        async with conn.execute("SELECT user_id FROM support_tickets WHERE id=? AND status='open'", (ticket_id,)) as cur:
            row = await cur.fetchone()
        if not row:
            return None
        await conn.execute('INSERT INTO support_messages(ticket_id,author_id,body) VALUES(?,?,?)', (ticket_id,actor_id,body))
        await conn.execute('UPDATE support_tickets SET admin_reply=?,replied_by=?,updated_at=CURRENT_TIMESTAMP WHERE id=?', (body,actor_id,ticket_id))
        await conn.commit()
        return int(row[0])


async def history(ticket_id, limit=5):
    await ensure_schema()
    async with aiosqlite.connect(config.DATABASE_PATH) as conn:
        async with conn.execute('SELECT body FROM support_messages WHERE ticket_id=? ORDER BY id DESC LIMIT ?', (ticket_id,limit)) as cur:
            return [row[0] for row in reversed(await cur.fetchall())]
