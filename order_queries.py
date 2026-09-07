"""Operational order filters over existing order and provisioning states."""
import aiosqlite
import config

FILTERS = {
    'pending': ('در انتظار بررسی', "status IN ('pending','submitted')"),
    'receipt': ('در انتظار رسید', "status='pending' AND receipt_file_id IS NULL"),
    'approval': ('در انتظار تأیید', "status='submitted'"),
    'approved': ('تأییدشده', "status='approved'"),
    'rejected': ('ردشده', "status='rejected'"),
    'failed': ('صدور ناموفق', "status='failed' OR rebecca_provision_state IN ('failed','uncertain')"),
    'completed': ('صدور تکمیل‌شده', "status='approved' AND issued_admin_id IS NOT NULL"),
    'all': ('همه سفارش‌ها', '1=1'),
}
STATUS_LABELS = {'pending': 'در انتظار رسید', 'submitted': 'در انتظار تأیید', 'approved': 'تأییدشده',
                 'rejected': 'ردشده', 'cancelled': 'لغوشده', 'failed': 'صدور ناموفق'}


async def list_orders(category='pending', page=0, page_size=10):
    if category not in FILTERS:
        raise ValueError('فیلتر نامعتبر')
    where = FILTERS[category][1]
    async with aiosqlite.connect(config.DATABASE_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute('SELECT COUNT(*) FROM orders WHERE '+where) as cur:
            total = int((await cur.fetchone())[0])
        pages = max(1, (total+page_size-1)//page_size)
        page = max(0,min(int(page),pages-1))
        async with conn.execute('SELECT * FROM orders WHERE '+where+' ORDER BY id DESC LIMIT ? OFFSET ?', (page_size,page*page_size)) as cur:
            return [dict(r) for r in await cur.fetchall()], total, page, pages
