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
    # Read-only Rebecca per-user monitoring. All three messages can be edited
    # in Telegram and can use Premium Emoji keys via the existing editor.
    'rebecca_user_quota_single': (
        '📦 <b>حجم کاربر به پایان رسید</b>\n\n'
        '👤 <code>{username}</code>\n'
        '🖥 پنل: {panel_name}\n'
        '📊 مصرف: {used_traffic} / {data_limit}\n\n'
        'برای مدیریت کاربر به بخش کاربران پنل مراجعه کنید.'
    ),
    'rebecca_user_expired_single': (
        '⏳ <b>اعتبار کاربر پایان یافت</b>\n\n'
        '👤 <code>{username}</code>\n'
        '🖥 پنل: {panel_name}\n'
        '📊 مصرف: {used_traffic} / {data_limit}'
    ),
    'rebecca_user_smart_digest': (
        '📦 <b>گزارش کاربران نیازمند بررسی</b>\n\n'
        '{count} رویداد جدید ثبت شد:\n{users_list}\n\n'
        'برای مدیریت، از بخش کاربران پنل استفاده کنید.'
    ),
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
    'rebecca_user_quota_single': 'Rebecca — اتمام حجم یک کاربر',
    'rebecca_user_expired_single': 'Rebecca — پایان اعتبار یک کاربر',
    'rebecca_user_smart_digest': 'Rebecca — گزارش هوشمند گروهی کاربران',
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

# Digital-store copy stays in the existing editable Premium UI catalog.
# Product-specific usage rules are separately snapshotted at order creation.
UI_MESSAGES.update({
    "fastly_home": "{emoji:shop} <b>اکانت‌های آماده</b>\n\nمدل موردنظرت رو انتخاب کن. قیمت و موجودی هر مدل پایینش مشخصه.",
    "fastly_product": "<b>{title}</b>\n\n{description}\n\nقیمت هر اکانت: <b>{price} تومان</b>\nموجودی: <b>{stock} عدد</b>\nضمانت: <b>{warranty}</b>",
    "fastly_order": "🧾 <b>سفارش #{order_id}</b>\n\n{title}\nتعداد: <b>{quantity} عدد</b>\nمبلغ: <b>{total} تومان</b>\nوضعیت: <b>{status}</b>",
    "fastly_kyc": "{emoji:kyc} <b>تأیید حساب</b>\n\nبرای خرید اکانت هم از همون احراز هویت یک‌باره ربات استفاده می‌کنیم. اگه قبلاً تأیید شدی، نیازی به ارسال دوباره مدرک نیست.\n\nبعد از تأیید، از «سفارش‌های من» پرداخت رو ادامه بده.",
    "fastly_payment": "💳 <b>پرداخت سفارش #{order_id}</b>\n\nمبلغ: <b>{total} تومان</b>\n\n<b>کارت‌های مقصد</b>\n{cards}\n\nبعد از واریز از کارت متعلق به خودت، تصویر رسید رو ثبت کن. رسید پس از تطبیق واقعی توسط مدیر تأیید می‌شه.",
    "fastly_receipt_received": "✅ رسید سفارش <b>#{order_id}</b> ثبت شد. بعد از بررسی و تأیید پرداخت، همین‌جا خبرت می‌کنیم.",
    "fastly_ready": "✅ پرداخت سفارش <b>#{order_id}</b> تأیید شد. اکانت‌ها آماده دریافت‌اند؛ از دکمه زیر ابتدا قوانین استفاده رو بخون.",
    "fastly_rules": "📋 <b>قوانین سفارش #{order_id}</b>\n\n{rules}\n\nبا انتخاب دکمه پایین، تأیید می‌کنی که این شرایط رو خوندی.",
    "fastly_claim_created": "🛡 درخواست تعویض <b>#{claim_id}</b> ثبت شد. نتیجه بررسی مدیر رو همین‌جا می‌فرستیم.",
    "fastly_out_of_stock": "⚠️ پرداخت سفارش <b>#{order_id}</b> تأیید شده، اما موجودی کافی برای تحویل نداریم. سفارش گم نشده؛ پشتیبانی باید موجودی یا بازپرداخت رو پیگیری کنه.",
    "fastly_rejected": "رسید سفارش <b>#{order_id}</b> تأیید نشد. برای بررسی پرداخت و ارسال مدرک صحیح، با پشتیبانی در ارتباط باش.",
})
UI_TITLES.update({
    "fastly_home": "فستلی — صفحه فروشگاه",
    "fastly_product": "فستلی — جزئیات محصول",
    "fastly_order": "فستلی — خلاصه سفارش",
    "fastly_kyc": "فستلی — احراز هویت پیش از پرداخت",
    "fastly_payment": "فستلی — راهنمای پرداخت",
    "fastly_receipt_received": "فستلی — دریافت رسید",
    "fastly_ready": "فستلی — سفارش آماده دریافت",
    "fastly_rules": "فستلی — قوانین استفاده",
    "fastly_claim_created": "فستلی — ثبت درخواست تعویض",
    "fastly_out_of_stock": "فستلی — کمبود موجودی پس از پرداخت",
    "fastly_rejected": "فستلی — رد رسید",
})
