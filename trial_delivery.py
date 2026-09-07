"""Private trial delivery storage and URL validation, independent of Telegram handlers."""
import json
import secrets
import time
from urllib.parse import urlsplit
import aiosqlite
import config


def connection_url(value):
    value = str(value or '').strip()
    try:
        parsed = urlsplit(value)
        if (parsed.scheme not in {'https', 'http'} or not parsed.hostname
                or parsed.username or parsed.password or any(ord(c) < 33 for c in value)):
            return None
        parsed.port
    except ValueError:
        return None
    return value


async def save_connections(user_id, result):
    links = list(dict.fromkeys(str(x).strip() for x in result.get('links', []) if str(x).strip()))
    token = secrets.token_hex(12)
    async with aiosqlite.connect(config.DATABASE_PATH) as conn:
        await conn.execute('CREATE TABLE IF NOT EXISTS trial_connections (token TEXT PRIMARY KEY, user_id INTEGER NOT NULL, links TEXT NOT NULL, expires INTEGER NOT NULL)')
        await conn.execute('DELETE FROM trial_connections WHERE expires<=?', (int(time.time()),))
        await conn.execute('INSERT INTO trial_connections VALUES(?,?,?,?)',
                           (token, int(user_id), json.dumps(links), int(result['expire_at'])))
        await conn.commit()
    return token


async def load_connections(token, user_id):
    async with aiosqlite.connect(config.DATABASE_PATH) as conn:
        try:
            async with conn.execute('SELECT links FROM trial_connections WHERE token=? AND user_id=? AND expires>?',
                                    (str(token), int(user_id), int(time.time()))) as cur:
                row = await cur.fetchone()
        except aiosqlite.OperationalError:
            return []
    return json.loads(row[0]) if row else []
