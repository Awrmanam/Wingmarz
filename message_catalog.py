"""Customer copy registered before the runtime editor snapshots its defaults."""
UI_MESSAGES = {
    'customer_home': 'خوش آمدید!\n\nاز کجا شروع کنیم؟',
    'support_home': '🎧 <b>پشتیبانی</b>\n\nچطور می‌توانیم کمکتان کنیم؟',
    'support_subject': 'عنوان کوتاه درخواست را ارسال کنید:',
    'support_body': 'متن درخواست را ارسال کنید:',
    'support_submitted': '✅ درخواست شما ثبت شد. پاسخ پشتیبانی همین‌جا ارسال می‌شود.',
    'support_reply': '🎧 <b>پاسخ پشتیبانی</b>\n\n{reply}',
    'sales_service_select': '🛒 <b>خرید پنل نمایندگی</b>\n\nنوع سرویس را انتخاب کنید:',
    'sales_empty': 'فعلاً سرویسی برای خرید آماده نیست.\nلطفاً کمی بعد دوباره سر بزنید یا با پشتیبانی تماس بگیرید.',
    'sales_plan_select': '<b>{service}</b>\n\nپلن موردنظر را انتخاب کنید:',
    'sales_duration_select': '<b>{service}</b>\n\nمدت سرویس را انتخاب کنید:',
}
UI_TITLES = {
    'customer_home': 'خوش‌آمدگویی مشتری', 'support_home': 'صفحه پشتیبانی',
    'support_subject': 'درخواست عنوان تیکت', 'support_body': 'درخواست متن تیکت',
    'support_submitted': 'ثبت موفق تیکت', 'support_reply': 'پاسخ پشتیبانی',
    'sales_service_select': 'انتخاب سرویس خرید', 'sales_empty': 'نبود سرویس آماده فروش',
    'sales_plan_select': 'انتخاب پلن', 'sales_duration_select': 'انتخاب مدت',
}

TRIAL_UI_DEFAULTS: dict[str, str] = {
    "trial_v2_root": (
        "🧪 <b>تست رایگان</b>\n\n"
        "نوع تستی که می‌خواهید دریافت کنید را انتخاب کنید."
    ),
    "trial_v2_config_select": (
        "🧪 <b>تست رایگان کانفیگ</b>\n\n"
        "نوع سرویس موردنظر را انتخاب کنید:"
    ),
    "trial_v2_panel_select": (
        "🧩 <b>تست پنل نمایندگی</b>\n\n"
        "نوع سرویس موردنظر را انتخاب کنید:"
    ),
    "trial_v2_config_success": (
        "✅ <b>کانفیگ تست شما آماده شد</b>\n\n"
        "📦 حجم: <b>{traffic}</b>\n"
        "⏱ اعتبار: <b>{minutes} دقیقه</b>\n\n"
        "برای اتصال از دکمه زیر استفاده کنید."
    ),
    "trial_v2_panel_username": (
        "🧩 <b>دریافت پنل تست</b>\n\n"
        "نام کاربری دلخواهتان را ارسال کنید.\n"
        "رمز عبور به‌صورت امن توسط ربات ساخته می‌شود.\n\n"
        "مثال: <code>arman_test</code>"
    ),
    "trial_v2_panel_success": (
        "✅ <b>پنل تست شما آماده شد</b>\n\n"
        "👤 نام کاربری: <code>{username}</code>\n"
        "🔑 رمز عبور: <code>{password}</code>\n"
        "⏱ اعتبار: <b>{hours} ساعت</b>\n\n"
        "برای ورود از دکمه زیر استفاده کنید."
    ),
}

UI_MESSAGES.update(TRIAL_UI_DEFAULTS)
