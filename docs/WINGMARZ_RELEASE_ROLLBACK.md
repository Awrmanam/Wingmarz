# Wingmarz — راهنمای انتشار ایمن، بکاپ، بررسی و بازگشت

> وضعیت: دستورالعمل آماده‌سازی؛ **این سند مجوز انتشار نیست**. تا زمانی که CI تماماً سبز و PR #70 تأیید/ادغام نشده است، هیچ دستور مربوط به سرور واقعی را اجرا نکنید. این راهنما هیچ بکاپی روی سرور تولید ایجاد نکرده است.

## پیش‌شرط و نقاط توقف

1. کار در GitHub با PR شماره 70 و برنچ `feat/unified-management-ux-20261113` انجام می‌شود. فقط برنچ `main` **ادغام‌شده و تأییدشده** قابل انتشار است.
2. قبل از تغییر، نام واحد systemd و مسیر واقعی سرویس و دیتابیس را **از روی همان سرور** مشخص کنید. README واحد نمونه `marzban-admin-bot.service` را نشان می‌دهد؛ این نام ممکن است در نصب شما متفاوت باشد.
3. یک پنجره نگهداری تعیین کنید؛ فقط یک نمونه polling و یک Scheduler مجاز است. قطعی Rebecca/Telegram نباید به «صفر شدن مصرف» تعبیر شود.
4. بکاپ SQLite باید با `sqlite3.Connection.backup` انجام شود؛ کپی ساده یک فایل در حالت WAL می‌تواند ناقص باشد.
5. ثبت baseline و صحت بکاپ، هر دو قبل از دست‌کاری DB اجباری‌اند. اطلاعات واقعی در CI تست نمی‌شوند.

## ۱) شناسایی نصب فعلی (فقط خواندن)

```bash
sudo systemctl list-units --all --type=service | grep -Ei 'wing|marz|bot'
sudo systemctl cat marzban-admin-bot.service  # نام واقعی را جایگزین کنید
# مسیر پروژه را با WorkingDirectory واحد بالا تطبیق بدهید
cd /path/to/your/Wingmarz
git status --short
git branch --show-current
git rev-parse HEAD
git remote -v
```

اگر `git status --short` خروجی دارد، آن را بدون بررسی پاک نکنید. فایل‌های `.env`، دیتابیس و توکن را نه در کامیت قرار دهید و نه در CI منتشر کنید.

## ۲) توقف سرویس و بکاپ سازگار با SQLite

مقادیر `PROJECT`، `SERVICE`، `DATABASE` و `BACKUP_DIR` را بر پایه **مسیر واقعی** تغییر دهید. `DATABASE` باید دقیقاً همان `DATABASE_PATH` در تنظیمات تولید باشد.

```bash
export PROJECT=/path/to/your/Wingmarz
export SERVICE=marzban-admin-bot.service
export DATABASE=/path/to/your/Wingmarz/bot_database.db
export BACKUP_DIR=/path/to/secure/wingmarz-backups/$(date -u +%Y%m%dT%H%M%SZ)
sudo install -d -m 700 "$BACKUP_DIR"
cd "$PROJECT"
git rev-parse HEAD | sudo tee "$BACKUP_DIR/deployed_commit.txt" >/dev/null
sudo systemctl stop "$SERVICE"
sudo systemctl is-active "$SERVICE"  # باید inactive باشد
sudo python3 - "$DATABASE" "$BACKUP_DIR/database.sqlite3" <<'PY'
import sqlite3, sys
src, dest = sys.argv[1:]
with sqlite3.connect(f"file:{src}?mode=ro", uri=True) as original:
    with sqlite3.connect(dest) as backup:
        original.backup(backup)
with sqlite3.connect(f"file:{dest}?mode=ro", uri=True) as check:
    assert check.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
print("Database snapshot and integrity verified:", dest)
PY
# اسرار و مجوزها را حفظ کنید؛ خروجی بکاپ باید فقط در مسیر امن بماند
sudo install -m 600 .env "$BACKUP_DIR/env.snapshot"
sudo sha256sum "$BACKUP_DIR/database.sqlite3" | sudo tee "$BACKUP_DIR/SHA256SUMS" >/dev/null
```

**توقف انتشار:** اگر بکاپ وجود ندارد، حجم صفر/غیرمنتظره دارد، `integrity_check` خطا می‌دهد یا نسخه Git نامشخص است، سرویس را طبق دستور قبل از تغییر فعال و مشکل را بررسی کنید؛ ادامه ندهید.

## ۳) تست پیش از نصب

```bash
cd "$PROJECT"
# ابتدا مطمئن شوید PR #70 در GitHub ادغام شده و CI سبز است
git fetch --prune origin
git switch main
git merge --ff-only origin/main
. .venv/bin/activate
python -m pip install -r requirements.txt
python -m compileall -q bot.py handlers payg_service.py rebecca_user_alerts.py scheduler.py
python -m pytest -q
```

در این مرحله تست‌ها باید با DB آزمایشی خود pytest اجرا شوند؛ هیچ `bot.py` یا ربات دوم را برای smoke-test در کنار تولید روشن نکنید. اگر تست‌ها خطا دادند، **سرویس با کد جدید روشن نشود**.

## ۴) فعال‌سازی کنترل‌شده

```bash
sudo systemctl start "$SERVICE"
sudo systemctl status "$SERVICE" --no-pager
sudo journalctl -u "$SERVICE" --since '5 minutes ago' --no-pager | tail -100
```

از داخل تلگرام با حساب SUDO: مرکز نمایندگان، مرکز ظاهر و محتوا، صف بازبینی PAYG، ترتیب دکمه‌ها و بازگشت صفحات را بررسی کنید. در حالت Rebecca مجوز KYC و جداسازی نمایندگان را با حساب‌های تست بررسی کنید. تغییرات PAYG/پنل روی مشتری واقعی انجام ندهید. مانیتورینگ اعلان کاربران با `REBECCA_USER_ALERTS_ENABLED` تا زمانی که baseline و API واقعی بررسی نشده‌اند opt-in بماند.

## ۵) بازگشت بدون Force Push

اگر خطای راه‌اندازی یا مهاجرت پیدا شد، سرویس را متوقف کنید و سرور را به همان نسخه ثبت‌شده برگردانید. **دیتابیس با بکاپ قبل از تغییر برگردانده می‌شود؛ این کار همه سفارش‌های ثبت‌شده پس از بکاپ را از بین می‌برد.** قبل از Restore داده‌های جدید را استخراج/آشتی دهید و رضایت لازم را بگیرید.

```bash
sudo systemctl stop "$SERVICE"
cd "$PROJECT"
BASE_COMMIT="$(sudo cat "$BACKUP_DIR/deployed_commit.txt")"
git fetch origin
git switch --detach "$BASE_COMMIT"  # رفرنس main و تاریخچه GitHub را عوض نمی‌کند
sudo python3 - "$BACKUP_DIR/database.sqlite3" "$DATABASE" <<'PY'
import sqlite3, sys
src, dest = sys.argv[1:]
with sqlite3.connect(f"file:{src}?mode=ro", uri=True) as source:
    with sqlite3.connect(dest) as target:
        source.backup(target)
with sqlite3.connect(dest) as check:
    assert check.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
PY
sudo systemctl start "$SERVICE"
sudo systemctl status "$SERVICE" --no-pager
```

قبل از بازگرداندن DB، از وضعیت فعلی نیز snapshot امن بگیرید تا بتوان تراکنش‌های فاصله انتشار تا بازگشت را آشتی داد. وقتی مشکل برطرف شد، پس از تأیید PR جدید به `main` برگردید، نه با `git reset --hard` یا `force push`.

## نکته خاص درباره مهاجرت رابط کاربری

`ui_presentation_migration_meta` انتقال نام‌های قدیمی منو را فقط یک بار انجام می‌دهد. ردیف‌های `styled_text_overrides` قدیمی عمداً حفظ می‌شوند تا rollback ممکن باشد؛ پس هنگام بازیابی نباید این ردیف‌ها را دستی حذف کرد. پس از rollback، تغییرات جدید UI و بررسی‌های مالیِ بعد از زمان بکاپ با دیتابیس قدیمی سازگار/قابل‌حفظ فرض نشوند؛ بازیابی باید در یک کپی staging آزموده شود.

## شرایط ممنوعیت انتشار

- خطای هر تست GitHub Actions، تست پرداخت، وضعیت بازبینی شمارنده یا مالکیت نمایندگان
- نامعلوم بودن سرویس یا چند نمونه فعال Scheduler/Polling
- نبود بکاپ سالم و قابل Restore
- اختلاف واقعی بین مالکیت Telegram و Rebecca
- ناقص بودن هماهنگی تعلیق‌های مالی/دستی، صف ارسال یا بازیابی خطاها

این سند راهنمای عملیاتی است؛ اثبات موفقیت تست Restore روی دیتابیس تولید یا آماده‌بودن نسخه محسوب نمی‌شود.
