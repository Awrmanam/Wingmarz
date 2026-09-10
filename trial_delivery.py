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


async def load_latest_connections(user_id, *, expires_after=None):
    """Return the newest still-valid private connection bundle for one user.

    ``trial_connections`` is intentionally user-bound. This helper is used only
    to reopen an already issued trial; it never creates or extends a trial.
    """
    now = int(time.time()) if expires_after is None else int(expires_after)
    async with aiosqlite.connect(config.DATABASE_PATH) as conn:
        try:
            async with conn.execute(
                'SELECT links FROM trial_connections '
                'WHERE user_id=? AND expires>? ORDER BY rowid DESC LIMIT 1',
                (int(user_id), now),
            ) as cur:
                row = await cur.fetchone()
        except aiosqlite.OperationalError:
            return []
    if not row:
        return []
    try:
        values = json.loads(row[0])
    except (TypeError, ValueError):
        return []
    return [str(item) for item in values if str(item).strip()] if isinstance(values, list) else []


async def load_active_trial(user_id, *, provider='rebecca'):
    """Load the latest unexpired config trial owned by ``user_id``.

    The authoritative issuance row lives in ``trial_issues``. Only the minimum
    delivery metadata required to show the same trial again is returned. The
    lookup is strictly owner-bound and never changes cooldown/expiry state.
    """
    now = int(time.time())
    async with aiosqlite.connect(config.DATABASE_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        try:
            async with conn.execute(
                '''
                SELECT id,user_id,provider,provider_username,service_id,
                       subscription_url,expire_at,created_at
                FROM trial_issues
                WHERE user_id=? AND provider=? AND expire_at>?
                ORDER BY id DESC
                LIMIT 1
                ''',
                (int(user_id), str(provider), now),
            ) as cur:
                row = await cur.fetchone()
        except aiosqlite.OperationalError:
            return None
    return dict(row) if row else None
