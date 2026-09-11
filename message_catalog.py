"""Customer copy registered before the runtime editor snapshots its defaults."""
UI_MESSAGES = {
    # Role-aware start/home sections. Each block is independently editable from
    # the Telegram text editor and may contain {emoji:key} Premium Emoji tokens.
    'home_public_title': '{emoji:home} <b>خوش آمدید</b>',
    'home_public_body': 'از اینجا می‌توانید پنل نمایندگی تهیه کنید یا تست رایگان بگیرید.',
    'home_public_footer': 'یکی از گزینه‌های زیر را انتخاب کنید.',
    'home_reseller_title': '{emoji:home} <b>مدیریت نمایندگی</b>',
    'home_reseller_body': 'پنل‌ها، کاربران، گزارش‌ها، خرید و تمدید از همین صفحه در دسترس شماست.',
    'home_reseller_footer': 'یکی از گزینه‌های زیر را انتخاب کنید.',

    # Kept for backwards compatibility with old already-rendered paths.
    'customer_home': 'خوش آمدید!\n\nاز کجا شروع کنیم؟',
    'support_home': '🎧 <b>پشتیبانی</b>\n\nچطور می‌توانیم کمکتان کنیم؟',
    'support_subject': '📝 عنوان کوتاه درخواست را ارسال کنید:',
    'support_body': '📝 متن درخواست را ارسال کنید:',
    'support_submitted': '✅ درخواست شما ثبت شد. پاسخ پشتیبانی همین‌جا ارسال می‌شود.',
    'support_reply': '🎧 <b>پاسخ پشتیبانی</b>\n\n{reply}',
    'tariffs_page': (
        '💰 <b>تعرفه‌ها</b>\n\n'
        'متن تعرفه‌ها و توضیحات فروش را از بخش «مدیریت متن‌ها» با محتوای دلخواه خودتان تنظیم کنید.'
    ),
    'sales_service_select': '🛒 <b>خرید پنل نمایندگی</b>\n\nپنل موردنظر را انتخاب کنید:',
    'sales_empty': 'فعلاً سرویسی برای خرید آماده نیست.\nلطفاً کمی بعد دوباره سر بزنید یا با پشتیبانی تماس بگیرید.',
    'sales_plan_select': '<b>{service}</b>\n\nپلن موردنظر را انتخاب کنید:',
    'sales_duration_select': '<b>{service}</b>\n\nمدت سرویس را انتخاب کنید:',
    'sales_plan_select_public': '🛒 <b>پلن‌های نمایندگی</b>\n\nپلن موردنظر را انتخاب کنید:',
    'sales_duration_select_public': '🛒 <b>پلن‌های نمایندگی</b>\n\nمدت موردنظر را انتخاب کنید:',
    'renew_panel_select': '🔄 <b>تمدید و افزایش</b>\n\nپنل موردنظر را انتخاب کنید:',
}
UI_TITLES = {
    'home_public_title': 'شروع مشتری — عنوان',
    'home_public_body': 'شروع مشتری — متن اصلی',
    'home_public_footer': 'شروع مشتری — راهنمای پایین',
    'home_reseller_title': 'شروع نماینده — عنوان',
    'home_reseller_body': 'شروع نماینده — متن اصلی',
    'home_reseller_footer': 'شروع نماینده — راهنمای پایین',
    'customer_home': 'خوش‌آمدگویی قدیمی مشتری',
    'support_home': 'صفحه پشتیبانی',
    'support_subject': 'درخواست عنوان تیکت',
    'support_body': 'درخواست متن تیکت',
    'support_submitted': 'ثبت موفق تیکت',
    'support_reply': 'پاسخ پشتیبانی',
    'tariffs_page': 'صفحه تعرفه‌ها',
    'sales_service_select': 'انتخاب پنل خرید',
    'sales_empty': 'نبود سرویس آماده فروش',
    'sales_plan_select': 'انتخاب پلن قدیمی',
    'sales_duration_select': 'انتخاب مدت قدیمی',
    'sales_plan_select_public': 'انتخاب پلن نمایندگی',
    'sales_duration_select_public': 'انتخاب مدت پلن نمایندگی',
    'renew_panel_select': 'انتخاب پنل برای تمدید',
}

TRIAL_UI_DEFAULTS: dict[str, str] = {
    "trial_v2_root": (
        "🧪 <b>تست رایگان</b>\n\n"
        "نوع تستی که می‌خواهید دریافت کنید را انتخاب کنید."
    ),
    "trial_v2_config_select": (
        "🧪 <b>تست رایگان کانفیگ</b>\n\n"
        "پنل موردنظر را انتخاب کنید:"
    ),
    "trial_v2_panel_select": (
        "🧩 <b>تست پنل نمایندگی</b>\n\n"
        "پنل موردنظر را انتخاب کنید:"
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

UI_MESSAGES.update({
    'sales_plan_summary': '🛒 <b>{plan_name}</b>\n\nحجم: <b>{traffic}</b>\nمدت: <b>{duration}</b>\nظرفیت: <b>{users} کاربر</b>\nقیمت: <b>{price} تومان</b>',
    'sales_username_prompt': 'نام کاربری دلخواه پنل را بفرستید.\n۳ تا ۳۲ کاراکتر؛ حروف انگلیسی، عدد، نقطه، خط تیره و آندرلاین.\nرمز عبور به‌صورت امن ساخته می‌شود.',
    'sales_discount_prompt': 'اگر کد تخفیف دارید وارد کنید؛ در غیر این صورت به پرداخت بروید.',
})
UI_TITLES.update({
    'sales_plan_summary': 'خلاصه مشخصات و قیمت پلن',
    'sales_username_prompt': 'درخواست نام کاربری خرید',
    'sales_discount_prompt': 'راهنمای کد تخفیف خرید',
})
