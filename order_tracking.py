"""Record approval outcomes without storing provider payloads or credentials."""
from functools import wraps
import re
import aiosqlite
import config
from authorization import is_staff


async def ensure_schema():
    async with aiosqlite.connect(config.DATABASE_PATH) as conn:
        await conn.execute('''CREATE TABLE IF NOT EXISTS order_attempt_results (
            order_id INTEGER PRIMARY KEY, outcome TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        await conn.commit()


async def observe_result(order_id):
    await ensure_schema()
    async with aiosqlite.connect(config.DATABASE_PATH) as conn:
        # Read and update while holding the write lock: a successful concurrent
        # attempt must never be overwritten by an older failed observation.
        await conn.execute('BEGIN IMMEDIATE')
        async with conn.execute('SELECT status FROM orders WHERE id=?', (order_id,)) as cur:
            row = await cur.fetchone()
        if not row or row[0] in {'rejected', 'cancelled'}:
            return
        outcome = 'issued' if row[0] == 'approved' else 'needs_attention'
        await conn.execute('''INSERT INTO order_attempt_results(order_id,outcome) VALUES(?,?)
            ON CONFLICT(order_id) DO UPDATE SET outcome=excluded.outcome,updated_at=CURRENT_TIMESTAMP''', (order_id,outcome))
        await conn.commit()


async def needs_attention(order_id):
    await ensure_schema()
    async with aiosqlite.connect(config.DATABASE_PATH) as conn:
        async with conn.execute("SELECT 1 FROM order_attempt_results WHERE order_id=? AND outcome='needs_attention'", (order_id,)) as cur:
            return await cur.fetchone() is not None


def track_approval(handler):
    @wraps(handler)
    async def wrapped(callback, *args, **kwargs):
        match = re.fullmatch(r'order_(?:approve|retry)_(\d+)', str(callback.data or ''))
        try:
            return await handler(callback, *args, **kwargs)
        finally:
            if match and is_staff(callback.from_user.id):
                try:
                    await observe_result(int(match.group(1)))
                except Exception:
                    # Observability is secondary to the original approval outcome.
                    pass
    return wrapped
