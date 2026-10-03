from __future__ import annotations

from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import config
from database import db
from payg_service import PaygError, PaygInsufficientFunds, payg_service
from utils.rebecca import credential_message


payg_router = Router(name="payg")


class PaygUserStates(StatesGroup):
    amount = State()
    receipt = State()


class PaygAdminStates(StatesGroup):
    rate = State()
    minimum = State()
    max_users = State()
    services = State()


def _money(value: int) -> str:
    return f"{int(value):,} تومان"


def _status_fa(status: str) -> str:
    return {
        "active": "🟢 فعال",
        "suspended": "🔴 متوقف به‌دلیل اتمام اعتبار",
        "migrated": "🔁 تبدیل‌شده به پلن کامل",
    }.get(str(status), str(status))


def _user_back(source: str) -> str:
    return "back_to_admin_main" if source == "a" else "public_back_main"


async def render_payg_offer(message: Message, user_id: int, provider: str, source: str) -> None:
    if provider != "rebecca":
        await message.edit_text(
            "این Provider هنوز برای PAYG آماده نشده است.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🔙 بازگشت", callback_data=f"panelprov:root:{source}")
            ]]),
        )
        return

    snapshot = await payg_service.account_snapshot(int(user_id), provider)
    settings = snapshot["settings"]
    if not settings or not int(settings["enabled"]):
        await message.edit_text(
            "⚡ <b>PAYG</b>\n\nدر حال حاضر فروش اعتباری برای این پنل فعال نیست.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🔙 بازگشت", callback_data=f"panelprov:select:{source}:{provider}")
            ]]),
        )
        return

    account = snapshot["account"]
    balance = int(snapshot["balance_toman"])
    rate = int(snapshot["rate_toman"])
    equivalent = float(snapshot["equivalent_gb"])
    minimum = int(settings["min_topup_toman"])
    lines = [
        "⚡ <b>پنل اعتباری PAYG</b>",
        "",
        f"💵 نرخ هر گیگ: <b>{_money(rate)}</b>",
        f"💰 موجودی کیف پول: <b>{_money(balance)}</b>",
        f"📦 معادل تقریبی موجودی: <b>{equivalent:.2f} GB</b>",
    ]
    rows: list[list[InlineKeyboardButton]] = []

    if account:
        used_gb = int(account["cumulative_usage_bytes"] or 0) / (1024 ** 3)
        lines.extend([
            f"📊 مصرف ثبت‌شده PAYG: <b>{used_gb:.3f} GB</b>",
            f"🧾 هزینه کسرشده: <b>{_money(int(account['charged_toman_total']))}</b>",
            f"📌 وضعیت: <b>{_status_fa(str(account['status']))}</b>",
        ])
        outstanding = int(snapshot["outstanding_toman"])
        if outstanding:
            lines.append(f"⚠️ بدهی مصرف ثبت‌شده: <b>{_money(outstanding)}</b>")
        rows.append([
            InlineKeyboardButton(text="➕ افزایش اعتبار", callback_data=f"payg:topup:{int(account['id'])}"),
            InlineKeyboardButton(text="📜 گردش کیف پول", callback_data=f"payg:history:{provider}"),
        ])
        rows.append([
            InlineKeyboardButton(text="🔄 تبدیل به پلن کامل", callback_data=f"payg:migrate:{int(account['id'])}")
        ])
    else:
        lines.extend([
            "",
            f"حداقل شارژ اولیه: <b>{_money(minimum)}</b>",
            "بعد از تأیید پرداخت، ادمین Rebecca به‌صورت خودکار ساخته و اطلاعات ورود ارسال می‌شود.",
        ])
        if balance >= minimum:
            rows.append([
                InlineKeyboardButton(text="⚡ ساخت پنل با موجودی فعلی", callback_data=f"payg:create:{provider}")
            ])
        rows.append([
            InlineKeyboardButton(text="💳 شارژ و شروع PAYG", callback_data=f"payg:start:{provider}")
        ])

    rows.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data=f"panelprov:select:{source}:{provider}")])
    await message.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


async def _render_payment(message: Message, topup_id: int, source: str = "p") -> None:
    topup = await payg_service.get_topup(topup_id)
    if not topup:
        await message.edit_text("❌ درخواست شارژ پیدا نشد.")
        return
    cards = await db.get_cards(only_active=True)
    lines = [
        "💳 <b>پرداخت شارژ کیف پول</b>",
        "",
        f"شناسه: <code>PAYG-{int(topup_id)}</code>",
        f"مبلغ: <b>{_money(int(topup['amount_toman']))}</b>",
        "",
        "مبلغ را به یکی از کارت‌های زیر واریز کنید و سپس رسید را ارسال کنید:",
    ]
    if cards:
        for card in cards:
            lines.append(
                f"• {escape(str(card.get('bank_name') or 'بانک'))} | "
                f"<code>{escape(str(card.get('card_number') or '-'))}</code> | "
                f"{escape(str(card.get('holder_name') or ''))}"
            )
    else:
        lines.append("⚠️ فعلاً کارت پرداخت فعالی ثبت نشده است.")
    rows = [[InlineKeyboardButton(text="✅ پرداخت کردم؛ ارسال رسید", callback_data=f"payg:markpaid:{int(topup_id)}")]]
    rows.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data=_user_back(source))])
    await message.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@payg_router.callback_query(F.data.startswith("payg:offer:"))
async def payg_offer(callback: CallbackQuery, state: FSMContext):
    parts = (callback.data or "").split(":")
    if len(parts) != 4 or parts[2] not in {"p", "a"}:
        await callback.answer("نامعتبر", show_alert=True)
        return
    await state.clear()
    await render_payg_offer(callback.message, callback.from_user.id, parts[3], parts[2])
    await callback.answer()


@payg_router.callback_query(F.data.startswith("payg:create:"))
async def payg_create_from_wallet(callback: CallbackQuery, state: FSMContext):
    provider = (callback.data or "").rsplit(":", 1)[-1]
    await state.clear()
    try:
        account = await payg_service.ensure_payg_account(callback.from_user.id, provider)
        admin = await db.get_admin_by_id(int(account["admin_id"]))
        if not admin:
            raise PaygError("local admin missing after provisioning")
        login_url = admin.login_url or config.REBECCA_LOGIN_URL or config.REBECCA_URL
        await callback.message.edit_text(
            credential_message(
                str(admin.marzban_username), str(admin.marzban_password), str(login_url), "PAYG Rebecca"
            )
            + "\n\nاز این لحظه هزینه بر اساس مصرف واقعی از کیف پول کم می‌شود.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🏠 صفحه اصلی", callback_data="back_to_admin_main")
            ]]),
        )
    except PaygInsufficientFunds as exc:
        await callback.answer(f"برای شروع {_money(exc.shortfall_toman)} دیگر شارژ کنید.", show_alert=True)
        return
    except Exception as exc:
        await callback.answer(f"ساخت پنل ناموفق بود: {str(exc)[:120]}", show_alert=True)
        return
    await callback.answer("پنل PAYG ساخته شد ✅")


@payg_router.callback_query(F.data.startswith("payg:start:"))
async def payg_start_topup(callback: CallbackQuery, state: FSMContext):
    provider = (callback.data or "").rsplit(":", 1)[-1]
    settings = await payg_service.get_settings(provider)
    if not settings or not int(settings["enabled"]):
        await callback.answer("PAYG فعال نیست.", show_alert=True)
        return
    await state.clear()
    await state.update_data(payg_provider=provider, payg_purpose="initial", payg_source="p")
    await state.set_state(PaygUserStates.amount)
    await callback.message.edit_text(
        f"💳 مبلغ شارژ اولیه را به تومان ارسال کنید.\n\nحداقل: <b>{_money(int(settings['min_topup_toman']))}</b>"
    )
    await callback.answer()


@payg_router.callback_query(F.data.startswith("payg:topup:"))
async def payg_account_topup(callback: CallbackQuery, state: FSMContext):
    try:
        account_id = int((callback.data or "").rsplit(":", 1)[-1])
    except ValueError:
        await callback.answer("نامعتبر", show_alert=True)
        return
    account = await payg_service.get_account(account_id)
    if not account or int(account["user_id"]) != int(callback.from_user.id):
        await callback.answer("حساب پیدا نشد.", show_alert=True)
        return
    settings = await payg_service.get_settings(str(account["provider"]))
    minimum = int((settings or {}).get("min_topup_toman") or 1)
    await state.clear()
    await state.update_data(
        payg_provider=str(account["provider"]), payg_purpose="wallet",
        payg_account_id=account_id, payg_source="a",
    )
    await state.set_state(PaygUserStates.amount)
    await callback.message.edit_text(
        f"➕ مبلغ افزایش اعتبار را به تومان ارسال کنید.\n\nحداقل شارژ: <b>{_money(minimum)}</b>"
    )
    await callback.answer()


@payg_router.message(PaygUserStates.amount, F.text)
async def payg_amount_received(message: Message, state: FSMContext):
    raw = (message.text or "").replace(",", "").replace(" ", "").strip()
    if not raw.isdecimal():
        await message.answer("مبلغ را فقط به‌صورت عدد و به تومان ارسال کنید.")
        return
    amount = int(raw)
    data = await state.get_data()
    provider = str(data.get("payg_provider") or "rebecca")
    settings = await payg_service.get_settings(provider)
    minimum = int((settings or {}).get("min_topup_toman") or 1)
    if amount < minimum:
        await message.answer(f"حداقل مبلغ شارژ {_money(minimum)} است.")
        return
    purpose = str(data.get("payg_purpose") or "wallet")
    topup_id = await payg_service.create_topup(
        message.from_user.id,
        amount,
        provider=provider,
        purpose=purpose,
        account_id=(int(data["payg_account_id"]) if data.get("payg_account_id") else None),
    )
    source = str(data.get("payg_source") or "p")
    await state.clear()
    await _render_payment(message, topup_id, source)


@payg_router.callback_query(F.data.startswith("payg:markpaid:"))
async def payg_mark_paid(callback: CallbackQuery, state: FSMContext):
    try:
        topup_id = int((callback.data or "").rsplit(":", 1)[-1])
    except ValueError:
        await callback.answer("نامعتبر", show_alert=True)
        return
    topup = await payg_service.get_topup(topup_id)
    if not topup or int(topup["user_id"]) != int(callback.from_user.id) or topup["status"] != "pending":
        await callback.answer("این درخواست قابل پرداخت نیست.", show_alert=True)
        return
    await state.clear()
    await state.update_data(payg_topup_id=topup_id)
    await state.set_state(PaygUserStates.receipt)
    await callback.message.edit_text("🧾 عکس رسید پرداخت را همین‌جا ارسال کنید.")
    await callback.answer()


@payg_router.message(PaygUserStates.receipt)
async def payg_receipt_received(message: Message, state: FSMContext):
    data = await state.get_data()
    topup_id = int(data.get("payg_topup_id") or 0)
    if not topup_id:
        await state.clear()
        return
    if not message.photo:
        await message.answer("لطفاً رسید را به‌صورت عکس ارسال کنید.")
        return
    file_id = message.photo[-1].file_id
    if not await payg_service.submit_topup_receipt(topup_id, message.from_user.id, file_id):
        await state.clear()
        await message.answer("این درخواست قبلاً بررسی شده یا معتبر نیست.")
        return
    topup = await payg_service.get_topup(topup_id)
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ تأیید شارژ", callback_data=f"paygadmin:approve:{topup_id}"),
        InlineKeyboardButton(text="❌ رد", callback_data=f"paygadmin:reject:{topup_id}"),
    ]])
    caption = (
        f"🧾 شارژ PAYG #{topup_id}\n"
        f"کاربر: <code>{message.from_user.id}</code>\n"
        f"مبلغ: <b>{_money(int(topup['amount_toman']))}</b>\n"
        f"نوع: {escape(str(topup['purpose']))}"
    )
    for sudo_id in config.SUDO_ADMINS:
        try:
            await message.bot.send_photo(sudo_id, photo=file_id, caption=caption, reply_markup=kb)
        except Exception:
            pass
    await state.clear()
    await message.answer("✅ رسید ارسال شد. پس از تأیید، اعتبار به کیف پول اضافه می‌شود.")


@payg_router.callback_query(F.data.startswith("payg:history:"))
async def payg_history(callback: CallbackQuery):
    provider = (callback.data or "").rsplit(":", 1)[-1]
    rows = await payg_service.wallet_history(callback.from_user.id, 15)
    balance = await payg_service.get_balance(callback.from_user.id)
    lines = ["📜 <b>گردش کیف پول</b>", "", f"موجودی: <b>{_money(balance)}</b>", ""]
    labels = {
        "topup": "شارژ",
        "payg_usage": "مصرف PAYG",
        "fixed_plan_purchase": "خرید پلن کامل",
        "migration_refund": "بازگشت وجه",
    }
    for item in rows:
        amount = int(item["amount_toman"])
        sign = "+" if amount > 0 else ""
        lines.append(f"• {labels.get(str(item['kind']), str(item['kind']))}: <b>{sign}{amount:,}</b> ت")
    if not rows:
        lines.append("هنوز تراکنشی ثبت نشده است.")
    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🔙 بازگشت", callback_data=f"payg:offer:a:{provider}")
        ]]),
    )
    await callback.answer()


@payg_router.callback_query(F.data.startswith("payg:migrate:"))
async def payg_migration_plans(callback: CallbackQuery):
    try:
        account_id = int((callback.data or "").rsplit(":", 1)[-1])
    except ValueError:
        await callback.answer("نامعتبر", show_alert=True)
        return
    account = await payg_service.get_account(account_id)
    if not account or int(account["user_id"]) != int(callback.from_user.id):
        await callback.answer("حساب پیدا نشد.", show_alert=True)
        return
    plans = await payg_service.list_migration_plans()
    if not plans:
        await callback.answer("فعلاً پلن کاملی برای تبدیل تعریف نشده است.", show_alert=True)
        return
    balance = await payg_service.get_balance(callback.from_user.id)
    lines = [
        "🔄 <b>تبدیل PAYG به پلن کامل</b>",
        "",
        f"موجودی کیف پول: <b>{_money(balance)}</b>",
        "اگر موجودی کافی نباشد فقط مابه‌التفاوت را پرداخت می‌کنید.",
    ]
    rows = []
    for plan in plans:
        rows.append([InlineKeyboardButton(
            text=f"{plan.name} · {int(plan.price):,} ت",
            callback_data=f"payg:migrateplan:{account_id}:{int(plan.id)}",
        )])
    rows.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="payg:offer:a:rebecca")])
    await callback.message.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


async def _deliver_migration(message: Message, result) -> None:
    await message.edit_text(
        "✅ <b>تبدیل سرویس با موفقیت انجام شد.</b>\n\n"
        + credential_message(
            result.username, result.password, result.login_url, result.plan_name
        )
        + "\n\nپنل PAYG قبلی غیرفعال شد و باقی‌مانده کیف پول شما محفوظ است.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🏠 صفحه اصلی", callback_data="back_to_admin_main")
        ]]),
    )


@payg_router.callback_query(F.data.startswith("payg:migrateplan:"))
async def payg_migration_select(callback: CallbackQuery):
    parts = (callback.data or "").split(":")
    if len(parts) != 4:
        await callback.answer("نامعتبر", show_alert=True)
        return
    try:
        account_id, plan_id = int(parts[2]), int(parts[3])
    except ValueError:
        await callback.answer("نامعتبر", show_alert=True)
        return
    try:
        migration, topup_id = await payg_service.prepare_migration(
            callback.from_user.id, account_id, plan_id
        )
        if topup_id:
            await _render_payment(callback.message, topup_id, "a")
            await callback.answer("مابه‌التفاوت را پرداخت کنید.")
            return
        result = await payg_service.execute_migration(int(migration["id"]))
        await _deliver_migration(callback.message, result)
    except PaygInsufficientFunds as exc:
        await callback.answer(f"کمبود موجودی: {_money(exc.shortfall_toman)}", show_alert=True)
        return
    except Exception as exc:
        await callback.answer(f"تبدیل انجام نشد: {str(exc)[:120]}", show_alert=True)
        return
    await callback.answer("تبدیل انجام شد ✅")


# ---------------------------------------------------------------------------
# Sudo PAYG settings + receipt approval
# ---------------------------------------------------------------------------

async def _render_admin_home(message: Message) -> None:
    settings = await payg_service.get_settings("rebecca")
    if not settings:
        await message.edit_text("تنظیمات PAYG در دسترس نیست.")
        return
    status = "🟢 فعال" if int(settings["enabled"]) else "⚪ غیرفعال"
    text = (
        "⚡ <b>مدیریت PAYG</b>\n\n"
        f"Provider: <b>Rebecca</b>\n"
        f"وضعیت فروش: <b>{status}</b>\n"
        f"نرخ هر GB: <b>{_money(int(settings['price_per_gb_toman']))}</b>\n"
        f"حداقل شارژ: <b>{_money(int(settings['min_topup_toman']))}</b>\n"
        f"حداکثر کاربر پنل: <b>{int(settings['max_users'])}</b>\n"
        f"Service IDs: <code>{escape(str(settings['service_ids'] or '-'))}</code>\n\n"
        "Sanaei فعلاً فقط اسکلت Provider دارد و API آن بعداً متصل می‌شود."
    )
    rows = [
        [InlineKeyboardButton(
            text=("⛔ غیرفعال کردن PAYG" if int(settings["enabled"]) else "✅ فعال کردن PAYG"),
            callback_data="paygadmin:toggle",
        )],
        [
            InlineKeyboardButton(text="💵 نرخ هر گیگ", callback_data="paygadmin:set:rate"),
            InlineKeyboardButton(text="💳 حداقل شارژ", callback_data="paygadmin:set:minimum"),
        ],
        [
            InlineKeyboardButton(text="👥 حد کاربر", callback_data="paygadmin:set:users"),
            InlineKeyboardButton(text="🔌 Service IDs", callback_data="paygadmin:set:services"),
        ],
        [InlineKeyboardButton(text="🔙 بازگشت", callback_data="sudo_menu_sales")],
    ]
    await message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@payg_router.callback_query(F.data == "paygadmin:home")
async def payg_admin_home(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in config.SUDO_ADMINS:
        await callback.answer("غیرمجاز", show_alert=True)
        return
    await state.clear()
    await _render_admin_home(callback.message)
    await callback.answer()


@payg_router.callback_query(F.data == "paygadmin:toggle")
async def payg_admin_toggle(callback: CallbackQuery):
    if callback.from_user.id not in config.SUDO_ADMINS:
        await callback.answer("غیرمجاز", show_alert=True)
        return
    settings = await payg_service.get_settings("rebecca")
    try:
        await payg_service.set_enabled("rebecca", not bool(int(settings["enabled"])))
    except PaygError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await _render_admin_home(callback.message)
    await callback.answer("ذخیره شد ✅")


async def _admin_set_state(callback: CallbackQuery, state: FSMContext, target: State, prompt: str) -> None:
    if callback.from_user.id not in config.SUDO_ADMINS:
        await callback.answer("غیرمجاز", show_alert=True)
        return
    await state.clear()
    await state.set_state(target)
    await callback.message.edit_text(prompt)
    await callback.answer()


@payg_router.callback_query(F.data == "paygadmin:set:rate")
async def payg_admin_set_rate(callback: CallbackQuery, state: FSMContext):
    await _admin_set_state(callback, state, PaygAdminStates.rate, "نرخ هر 1GB را به تومان ارسال کنید:")


@payg_router.callback_query(F.data == "paygadmin:set:minimum")
async def payg_admin_set_minimum(callback: CallbackQuery, state: FSMContext):
    await _admin_set_state(callback, state, PaygAdminStates.minimum, "حداقل مبلغ شارژ را به تومان ارسال کنید:")


@payg_router.callback_query(F.data == "paygadmin:set:users")
async def payg_admin_set_users(callback: CallbackQuery, state: FSMContext):
    await _admin_set_state(callback, state, PaygAdminStates.max_users, "حداکثر تعداد کاربر پنل PAYG را ارسال کنید:")


@payg_router.callback_query(F.data == "paygadmin:set:services")
async def payg_admin_set_services(callback: CallbackQuery, state: FSMContext):
    await _admin_set_state(
        callback, state, PaygAdminStates.services,
        "Service IDهای Rebecca را با کاما ارسال کنید. مثال: <code>1,2,3</code>",
    )


async def _numeric_setting(message: Message, state: FSMContext, field: str, minimum: int = 1) -> None:
    if message.from_user.id not in config.SUDO_ADMINS:
        return
    raw = (message.text or "").replace(",", "").replace(" ", "").strip()
    if not raw.isdecimal() or int(raw) < minimum:
        await message.answer("عدد معتبر ارسال کنید.")
        return
    await payg_service.update_settings("rebecca", **{field: int(raw)})
    await state.clear()
    await message.answer("✅ ذخیره شد.")


@payg_router.message(PaygAdminStates.rate, F.text)
async def payg_admin_rate_value(message: Message, state: FSMContext):
    await _numeric_setting(message, state, "price_per_gb_toman", 1)


@payg_router.message(PaygAdminStates.minimum, F.text)
async def payg_admin_minimum_value(message: Message, state: FSMContext):
    await _numeric_setting(message, state, "min_topup_toman", 1)


@payg_router.message(PaygAdminStates.max_users, F.text)
async def payg_admin_users_value(message: Message, state: FSMContext):
    await _numeric_setting(message, state, "max_users", 1)


@payg_router.message(PaygAdminStates.services, F.text)
async def payg_admin_services_value(message: Message, state: FSMContext):
    if message.from_user.id not in config.SUDO_ADMINS:
        return
    try:
        await payg_service.update_settings("rebecca", service_ids=(message.text or "").strip())
    except Exception as exc:
        await message.answer(f"مقدار نامعتبر است: {str(exc)[:120]}")
        return
    await state.clear()
    await message.answer("✅ Service IDها ذخیره شدند.")


@payg_router.callback_query(F.data.startswith("paygadmin:approve:"))
async def payg_admin_approve(callback: CallbackQuery):
    if callback.from_user.id not in config.SUDO_ADMINS:
        await callback.answer("غیرمجاز", show_alert=True)
        return
    try:
        topup_id = int((callback.data or "").rsplit(":", 1)[-1])
        topup = await payg_service.approve_topup(topup_id, callback.from_user.id)
        continuation = await payg_service.post_topup_approval(topup_id)
        balance = await payg_service.get_balance(int(topup["user_id"]))
        user_text = f"✅ پرداخت تأیید شد.\n💰 موجودی کیف پول: <b>{_money(balance)}</b>"
        if continuation.get("account"):
            account = continuation["account"]
            admin = await db.get_admin_by_id(int(account["admin_id"]))
            if admin:
                login_url = admin.login_url or config.REBECCA_LOGIN_URL or config.REBECCA_URL
                user_text += "\n\n" + credential_message(
                    str(admin.marzban_username), str(admin.marzban_password), str(login_url), "PAYG Rebecca"
                )
        migration = continuation.get("migration")
        if migration:
            user_text += "\n\n✅ PAYG شما به پلن کامل تبدیل شد.\n\n" + credential_message(
                migration.username, migration.password, migration.login_url, migration.plan_name
            )
        billing = continuation.get("billing")
        if billing and billing.old_status == "suspended" and billing.new_status == "active":
            user_text += "\n\n🟢 پنل PAYG شما مجدداً فعال شد."
        try:
            await callback.bot.send_message(int(topup["user_id"]), user_text)
        except Exception:
            pass
        await callback.message.edit_caption(
            caption=(callback.message.caption or "") + "\n\n✅ تأیید شد",
            reply_markup=None,
        )
        await callback.answer("شارژ تأیید شد ✅")
    except Exception as exc:
        # Payment approval may already have credited the wallet; never silently
        # retry the financial mutation. The idempotent approval function keeps
        # the balance safe and this error is surfaced for operational follow-up.
        await callback.answer(f"پرداخت ثبت شد/نیاز به پیگیری: {str(exc)[:120]}", show_alert=True)


@payg_router.callback_query(F.data.startswith("paygadmin:reject:"))
async def payg_admin_reject(callback: CallbackQuery):
    if callback.from_user.id not in config.SUDO_ADMINS:
        await callback.answer("غیرمجاز", show_alert=True)
        return
    try:
        topup_id = int((callback.data or "").rsplit(":", 1)[-1])
    except ValueError:
        await callback.answer("نامعتبر", show_alert=True)
        return
    topup = await payg_service.get_topup(topup_id)
    if not topup or not await payg_service.reject_topup(topup_id, callback.from_user.id):
        await callback.answer("این درخواست دیگر قابل رد نیست.", show_alert=True)
        return
    try:
        await callback.bot.send_message(int(topup["user_id"]), "❌ رسید شارژ شما تأیید نشد.")
    except Exception:
        pass
    await callback.message.edit_caption(
        caption=(callback.message.caption or "") + "\n\n❌ رد شد",
        reply_markup=None,
    )
    await callback.answer("رد شد")
