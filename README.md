# ربات تلگرام مدیریت ادمین‌های مرزبان (Marzban)

یک ربات تلگرام حرفه‌ای (Aiogram 3) برای مدیریت نمایندگان (ادمین‌های غیرسودو) در مرزبان، با مانیتورینگ هوشمند، هشدار مرحله‌ای، و اعمال خودکار محدودیت‌ها.

## چی کار می‌کنه؟
- برای سودو:
  - ➕ ساخت/حذف ادمین با محدودیت‌های شخصی‌سازی‌شده (تعداد کاربر، زمان، حجم)
  - ✏️ ویرایش پنل‌ها، 🧹 پاکسازی کاربران منقضی/کم‌سهمیه، ♻️ ریست مصرف
  - 📋 دیدن لیست و وضعیت همه پنل‌ها، 🔄 فعال/غیرفعال کردن پنل‌ها
  - 💳 مدیریت فروش: کارت‌ها، پلن‌ها، تعرفه تمدید، ست‌کردن Login URL
  - 📢 مدیریت کانال‌های جوین اجباری
- برای ادمین معمولی:
  - 👤 اطلاعات حساب و محدودیت‌ها، 📈 گزارش‌گیری لحظه‌ای
  - 👥 لیست کاربران، 🔄 فعال‌سازی مجدد کاربران وقتی محدودیت اجازه بده
- هوشمند:
  - 🕐 مانیتورینگ خودکار (پیش‌فرض هر ۱۰ دقیقه)
  - ⚠️ هشدار مرحله‌ای در 60%، 70%، 80%، 90% برای زمان/تعداد کاربر/حجم
  - 🚫 در 100%: غیرفعال‌سازی خودکار پنل، تغییر پسورد پنل، غیرفعالسازی کاربران
  - 📨 نوتیف به سودو (با پسورد جدید) و به خود ادمین هنگام غیرفعالی، و هنگام فعال‌سازی مجدد

## پیش‌نیازها
- توکن ربات تلگرام از BotFather
- دسترسی به پنل مرزبان (URL + یوزرنیم/پسورد ادمین اصلی)
- آیدی تلگرام سودو ادمین‌ها (از `@userinfobot` بگیرید)

## نصب و راه‌اندازی (دو روش)

### روش ۱: با Docker (پیشنهادی و دائمی)
1) کلون پروژه و ورود:
```bash
git clone https://github.com/wings-iran/Wingmarz.git
cd Wingmarz
```
2) فایل `.env` بسازید:
```bash
cat > .env << 'EOF'
BOT_TOKEN=123456789:AA...
MARZBAN_URL=https://panel.example.com
MARZBAN_USERNAME=admin
MARZBAN_PASSWORD=your_password
SUDO_ADMINS=111111111,222222222
MONITORING_INTERVAL=600
WARNING_THRESHOLD=0.8
AUTO_DELETE_EXPIRED_USERS=false
EOF
```
3) پوشه‌های دیتا/لاگ و دسترسی‌ها:
```bash
mkdir -p data logs
sudo chown -R $USER:$USER data logs
```
4) اجرا (دائمی با restart):
```bash
docker compose up -d --build
```
5) لاگ‌ها:
```bash
docker compose logs -f | cat
```
نکته: اگر Pull ایمیج از Docker Hub خطا داد (403)، برای Docker daemon mirror تنظیم کنید یا ایمیج base را آفلاین load کنید.

### روش ۲: بدون Docker (virtualenv + systemd برای اجرای دائمی)
1) نصب پیش‌نیازها و ساخت محیط مجازی:
```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip
cd ~/ADMIN
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```
2) ساخت `.env` کنار پروژه (مثل بالا).
3) تست اجرا:
```bash
python3 bot.py
```
4) اجرای دائمی با systemd:
```bash
sudo tee /etc/systemd/system/marzban-admin-bot.service > /dev/null << 'EOF'
[Unit]
Description=Marzban Admin Telegram Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=%i
WorkingDirectory=/root/ADMIN
EnvironmentFile=/root/ADMIN/.env
ExecStart=/root/ADMIN/.venv/bin/python3 /root/ADMIN/bot.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable marzban-admin-bot
sudo systemctl start marzban-admin-bot
sudo systemctl status marzban-admin-bot --no-pager
journalctl -u marzban-admin-bot -f -n 200 | cat
```
توجه: مسیرها (`/root/ADMIN`) را مطابق سرور خودت تنظیم کن و مطمئن شو `.env` و venv در همان مسیرند.

## تنظیمات مهم
- `.env`:
  - `BOT_TOKEN`: توکن ربات
  - `MARZBAN_URL`: آدرس پنل مرزبان (https)
  - `MARZBAN_USERNAME`/`MARZBAN_PASSWORD`: ادمین اصلی مرزبان
  - `SUDO_ADMINS`: آیدی‌های سودو با کاما جدا
  - `MONITORING_INTERVAL`: بازه مانیتور (ثانیه)
  - `WARNING_THRESHOLD`: آستانه هشدار پیش‌فرض (مثلاً 0.8)
  - `AUTO_DELETE_EXPIRED_USERS`: پاکسازی خودکار کاربران قدیمی (true/false)

## استفاده
- سودو: `/start` → منوی دسته‌بندی‌شده (پنل‌ها، پاکسازی، فروش/مالی، تنظیمات، گزارشات)
- ادمین معمولی: `/start` → منوی ادمین (اطلاعات من/گزارش من/کاربران من/فعالسازی کاربران و خرید/تمدید)

## مانیتورینگ و هشدارها
- هر اجرا:
  - درصد استفاده زمان/کاربر/حجم محاسبه می‌شود.
  - در 60/70/80/90% برای هر مورد هشدار می‌رود (به ادمین).
  - در 100%: پنل غیرفعال، پسورد پنل تصادفی، کاربران غیرفعال، نوتیف به سودو (با پسورد جدید) و به خود ادمین.

## عیب‌یابی سریع
- توکن بات: مقدار `BOT_TOKEN` باید در `.env` درست باشد. اگر «YOUR_BOT_TOKEN» دیدی یعنی لود نشده.
- دیتابیس: مسیر `DATABASE_PATH` (پیش‌فرض `/app/data/bot_database.db` در Docker) باید قابل نوشتن باشد.
- لاگ‌ها: `bot.log` و لاگ سرویس/کانتینر را چک کن.

## حذف و پاک‌سازی
### Docker
```bash
cd ~/ADMIN
docker compose down
rm -rf data logs
```
### Systemd + venv
```bash
sudo systemctl stop marzban-admin-bot
sudo systemctl disable marzban-admin-bot
sudo rm -f /etc/systemd/system/marzban-admin-bot.service
sudo systemctl daemon-reload
cd ~
rm -rf ~/ADMIN
```

— هر جا گیر کردی، لاگ‌ها را بفرست تا دقیقاً همان مرحله را برات درست کنیم. موفق باشی! 🚀
# Panel provider

Marzban remains the default panel. To issue purchased reseller plans in Rebecca,
configure the bot process (do not commit secrets):

```env
PANEL_PROVIDER=rebecca
REBECCA_URL=https://panel.example.com
REBECCA_BEARER_TOKEN=your-personal-api-key
REBECCA_LOGIN_URL=https://panel.example.com
```

Rebecca issuance uses `POST /api/admin` with a bearer personal API key and
always explicitly requests the `standard` role. Existing global login URL
configuration, when present, continues to take precedence.
