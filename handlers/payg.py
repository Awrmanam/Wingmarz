from __future__ import annotations

from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import config
from database import db
from payg_service import PaygError, PaygInsufficientFunds, payg_service
from group_ops_service import group_ops_service
from group_ops_events import mirror_payg_receipt
from payg_kyc_gate import payg_kyc_gate
from trust_service import trust_service
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
    discount_customer = State()
    discount_policy = State()
    discount_review_reason = State()
    provision_review_reason = State()


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


async def _render_payment(message: Message, topup_id: int, source: str = "p", *, edit: bool = True) -> None:
    topup = await payg_service.get_topup(topup_id)
    if not topup:
        if edit:
            await message.edit_text("❌ درخواست شارژ پیدا نشد.")
        else:
            await message.answer("❌ درخواست شارژ پیدا نشد.")
        return
    from purchase_kyc_flow import require_checkout_kyc
    if await require_checkout_kyc(
        message, telegram_user_id=int(topup["user_id"]),
        kind="payg", reference_id=int(topup_id), source=source, edit=edit,
    ):
        return
    cards = await db.get_cards(only_active=True)
    kyc_needed = (
        str(topup.get("purpose") or "") == "initial"
        and await payg_kyc_gate.requires_kyc(int(topup["user_id"]))
    )
    lines = [
        "💳 <b>پرداخت شارژ کیف پول</b>",
        "",
        f"شناسه: <code>PAYG-{int(topup_id)}</code>",
        f"مبلغ: <b>{_money(int(topup['amount_toman']))}</b>",
        "",
        "مبلغ را به یکی از کارت‌های زیر واریز کنید و سپس رسید را ارسال کنید:",
        "",
        (
            "🪪 <b>توجه:</b> بعد از تأیید پرداخت اولیه، ساخت PAYG تا تأیید KYC متوقف می‌ماند "
            "و مبلغ تا آن زمان وارد کیف پول قابل‌مصرف نمی‌شود."
            if kyc_needed else
            "✅ این شارژ پس از تأیید پرداخت طبق روال عادی اعمال می‌شود."
        ),
    ]
    from purchase_kyc_flow import approved_payment_card_note
    lines.extend(("", await approved_payment_card_note(int(topup["user_id"])), ""))
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
    if edit:
        await message.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    else:
        await message.answer("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@payg_router.callback_query(F.data.startswith("payg:offer:"))
async def payg_offer(callback: CallbackQuery, state: FSMContext):
    parts = (callback.data or "").split(":")
    if len(parts) != 4 or parts[2] not in {"p", "a"}:
        await callback.answer("نامعتبر", show_alert=True)
        return
    await state.clear()
    # Old provider-wide offer is no longer a saleable product.
    from handlers.payg_offers import render_payg_storefront
    await render_payg_storefront(callback.message, parts[2])
    await callback.answer()


@payg_router.callback_query(F.data.startswith("payg:create:"))
async def payg_create_from_wallet(callback: CallbackQuery, state: FSMContext):
    """Stale buttons cannot bypass KYC or the durable Rebecca reservation."""
    await state.clear()
    from handlers.payg_offers import render_payg_storefront
    await render_payg_storefront(callback.message, "a")
    await callback.answer("برای ساخت پنل، سرویس Rebecca را از مسیر جدید انتخاب کنید.")


@payg_router.callback_query(F.data.startswith("payg:start:"))
async def payg_start_topup(callback: CallbackQuery, state: FSMContext):
    """Retire provider-wide top-ups; preserve historical account recharge."""
    await state.clear()
    from handlers.payg_offers import render_payg_storefront
    await render_payg_storefront(callback.message, "a")
    await callback.answer("برای خرید PAYG، یک سرویس مشخص انتخاب کنید.")


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
    await _render_payment(message, topup_id, source, edit=False)


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
    from purchase_kyc_flow import require_checkout_kyc
    if await require_checkout_kyc(
        callback.message, telegram_user_id=int(callback.from_user.id),
        kind="payg", reference_id=topup_id, edit=True,
    ):
        await callback.answer()
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
    await mirror_payg_receipt(
        message.bot,
        topup=topup,
        topup_id=topup_id,
        user=message.from_user,
        file_id=file_id,
        reply_markup=kb,
    )
    caption = (
        f"🧾 شارژ PAYG #{topup_id}\n"
        f"کاربر: <code>{message.from_user.id}</code>\n"
        f"مبلغ: <b>{_money(int(topup['amount_toman']))}</b>"
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
    try:
        plans = await payg_service.migration_plans_for_account(account_id)
    except PaygError:
        await callback.answer("سرویس Rebecca این حساب قدیمی باید قبل از تبدیل تأیید شود.", show_alert=True)
        return
    if not plans:
        await callback.answer("برای همین سرویس پلن کامل فعالی وجود ندارد.", show_alert=True)
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
        [InlineKeyboardButton(text="🎯 تخفیف اختصاصی مشتریان", callback_data="paygadmin:discounts")],
        [InlineKeyboardButton(text="🔙 مالی و پرداخت‌ها", callback_data="ops:hub:finance")],
    ]
    await message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@payg_router.callback_query(F.data == "paygadmin:home")
async def payg_admin_home(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in config.SUDO_ADMINS:
        await callback.answer("غیرمجاز", show_alert=True)
        return
    await state.clear()
    from handlers.payg_offers import render_admin_home
    await render_admin_home(callback.message)
    await callback.answer()


def _discount_owner(user_id: int) -> bool:
    import os
    owner = os.getenv("PAYG_OWNER_ID", "").strip()
    return owner.isdecimal() and int(owner) == int(user_id)


def _discount_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔎 جست‌وجوی مشتری", callback_data="paygadmin:discount:search")],
        [InlineKeyboardButton(text="👥 فهرست مشتریان", callback_data="paygadmin:discount:list:0")],
        [InlineKeyboardButton(text="🧩 عملیات نیمه‌کاره Rebecca", callback_data="paygadmin:provision:pending")],
        [InlineKeyboardButton(text="🔙 تعرفه‌های PAYG", callback_data="paygsvcadmin:home")],
    ])


async def _discount_customer_page(message: Message, user_id: int) -> None:
    import aiosqlite
    await payg_service.ensure_schema()
    async with aiosqlite.connect(payg_service.db_path) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            "SELECT id,status,price_per_gb_toman FROM payg_accounts "
            "WHERE user_id=? ORDER BY id DESC LIMIT 15", (int(user_id),),
        ) as cur:
            accounts = [dict(row) for row in await cur.fetchall()]
    if not accounts:
        await message.answer("برای این شناسه، حساب PAYG پیدا نشد.",reply_markup=_discount_menu())
        return
    rows = [[InlineKeyboardButton(
        text=f"حساب #{a['id']} · {a['status']}",
        callback_data=f"paygadmin:discount:account:{a['id']}",
    )] for a in accounts]
    rows.append([InlineKeyboardButton(text="🔙 بازگشت",callback_data="paygadmin:discounts")])
    await message.answer(
        f"🎯 حساب‌های PAYG کاربر <code>{user_id}</code>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@payg_router.callback_query(F.data == "paygadmin:discounts")
async def payg_discount_home(callback: CallbackQuery, state: FSMContext):
    if not _discount_owner(callback.from_user.id):
        await callback.answer("فقط مالک مجاز است.",show_alert=True)
        return
    await state.clear()
    await callback.message.edit_text("🎯 <b>مدیریت تخفیف PAYG</b>",reply_markup=_discount_menu())
    await callback.answer()


@payg_router.callback_query(F.data == "paygadmin:provision:pending")
async def payg_provision_pending(callback: CallbackQuery,state: FSMContext):
    if not _discount_owner(callback.from_user.id):
        await callback.answer("غیرمجاز",show_alert=True)
        return
    from payg_offers_service import payg_offer_service
    try:
        operations = await payg_offer_service.list_pending_provisions(callback.from_user.id)
    except Exception as exc:
        await callback.answer(f"خطای بررسی: {str(exc)[:100]}",show_alert=True)
        return
    await state.clear()
    rows = [[InlineKeyboardButton(
        text=f"#{op['id']} · {op['remote_username']} · {op['status']}",
        callback_data=f"paygadmin:provision:inspect:{op['id']}",
    )] for op in operations[:25]]
    rows.append([InlineKeyboardButton(text="بازگشت",callback_data="paygadmin:discounts")])
    await callback.message.edit_text(
        f"🧩 عملیات نیمه‌کاره Rebecca: {len(operations)} مورد\n"
        "تطبیق فقط با تأیید مالک و احراز مالکیت سرویس مجاز است.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@payg_router.callback_query(F.data.startswith("paygadmin:provision:inspect:"))
async def payg_provision_inspect(callback: CallbackQuery,state: FSMContext):
    if not _discount_owner(callback.from_user.id):
        await callback.answer("غیرمجاز",show_alert=True)
        return
    from payg_offers_service import payg_offer_service
    operation_id = int(callback.data.rsplit(":",1)[-1])
    try:
        result = await payg_offer_service.inspect_incomplete_provisions()
        match = next((x for x in result if x["id"] == operation_id),None)
    except Exception as exc:
        await callback.answer(f"Rebecca در دسترس نیست: {str(exc)[:100]}",show_alert=True)
        return
    if not match:
        await callback.answer("عملیات یافت نشد",show_alert=True)
        return
    await state.clear()
    await callback.message.edit_text(
        f"🧩 عملیات #{operation_id}\nوضعیت بررسی: {escape(match['status'])}\n"
        "اتصال فقط در صورت تطبیق مالکیت و سطح دسترسی Rebecca انجام می‌شود.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="بررسی و اتصال موجود",callback_data=f"paygadmin:provision:adopt:{operation_id}")],
            [InlineKeyboardButton(text="بازگشت",callback_data="paygadmin:provision:pending")],
        ]),
    )
    await callback.answer()


@payg_router.callback_query(F.data.startswith("paygadmin:provision:adopt:"))
async def payg_provision_adopt_prompt(callback: CallbackQuery,state: FSMContext):
    if not _discount_owner(callback.from_user.id):
        await callback.answer("غیرمجاز",show_alert=True)
        return
    operation_id = int(callback.data.rsplit(":",1)[-1])
    await state.clear()
    await state.update_data(provision_operation=operation_id)
    await state.set_state(PaygAdminStates.provision_review_reason)
    await callback.message.edit_text(
        f"برای عملیات #{operation_id} علت تطبیق را حداقل در ۱۲ نویسه ارسال کنید. "
        "این عملیات هیچ سرویس جدیدی در Rebecca ایجاد نمی‌کند.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="انصراف",callback_data="paygadmin:provision:pending"),
        ]]),
    )
    await callback.answer()


@payg_router.message(PaygAdminStates.provision_review_reason,F.text)
async def payg_provision_adopt_reason(message: Message,state: FSMContext):
    if not _discount_owner(message.from_user.id):
        return
    reason = (message.text or "").strip()
    if len(reason) < 12:
        await message.answer("علت باید حداقل ۱۲ نویسه باشد.")
        return
    data = await state.get_data()
    operation_id = int(data["provision_operation"])
    await state.update_data(provision_reason=reason)
    await state.set_state(None)
    await message.answer(
        f"⚠️ تأیید اتصال عملیات #{operation_id} به سفارش موجود پس از "
        f"بررسی مالکیت Rebecca\nعلت: {escape(reason[:250])}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="تأیید نهایی",callback_data=f"paygadmin:provision:confirm:{operation_id}")],
            [InlineKeyboardButton(text="انصراف",callback_data="paygadmin:provision:pending")],
        ]),
    )


@payg_router.callback_query(F.data.startswith("paygadmin:provision:confirm:"))
async def payg_provision_adopt_confirm(callback: CallbackQuery,state: FSMContext):
    if not _discount_owner(callback.from_user.id):
        await callback.answer("غیرمجاز",show_alert=True)
        return
    from payg_offers_service import payg_offer_service
    operation_id = int(callback.data.rsplit(":",1)[-1])
    data = await state.get_data()
    if int(data.get("provision_operation",0)) != operation_id or not data.get("provision_reason"):
        await callback.answer("درخواست تأیید منقضی شده است",show_alert=True)
        return
    try:
        account = await payg_offer_service.adopt_verified_provision(
            operation_id,callback.from_user.id,reason=data["provision_reason"],
        )
    except Exception as exc:
        await callback.answer(f"اتصال انجام نشد: {str(exc)[:120]}",show_alert=True)
        return
    await state.clear()
    await callback.message.edit_text(
        f"اتصال تأیید شد · حساب PAYG #{account['id']} · بدون ساخت یا برداشت تکراری",
        reply_markup=_discount_menu(),
    )
    await callback.answer()


@payg_router.callback_query(F.data.startswith("paygadmin:discount:list:"))
async def payg_discount_customer_list(callback: CallbackQuery):
    if not _discount_owner(callback.from_user.id):
        await callback.answer("غیرمجاز",show_alert=True)
        return
    try:
        page = int(callback.data.rsplit(":",1)[-1])
        if page < 0 or page > 10000:
            raise ValueError
    except ValueError:
        await callback.answer("صفحه نامعتبر",show_alert=True)
        return
    import aiosqlite
    await payg_service.ensure_schema()
    async with aiosqlite.connect(payg_service.db_path) as conn:
        async with conn.execute(
            "SELECT DISTINCT user_id FROM payg_accounts "
            "ORDER BY user_id LIMIT 11 OFFSET ?",(page*10,),
        ) as cur:
            ids = [int(row[0]) for row in await cur.fetchall()]
    rows = [[InlineKeyboardButton(
        text=f"👤 {uid}",callback_data=f"paygadmin:discount:user:{uid}",
    )] for uid in ids[:10]]
    navigation = []
    if page:
        navigation.append(InlineKeyboardButton(
            text="قبلی",callback_data=f"paygadmin:discount:list:{page-1}",
        ))
    if len(ids) > 10:
        navigation.append(InlineKeyboardButton(
            text="بعدی",callback_data=f"paygadmin:discount:list:{page+1}",
        ))
    if navigation:
        rows.append(navigation)
    rows.append([InlineKeyboardButton(text="🔙 بازگشت",callback_data="paygadmin:discounts")])
    await callback.message.edit_text(
        f"👥 مشتریان PAYG · صفحه {page+1}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@payg_router.callback_query(F.data.startswith("paygadmin:discount:user:"))
async def payg_discount_user_select(callback: CallbackQuery):
    if not _discount_owner(callback.from_user.id):
        await callback.answer("غیرمجاز",show_alert=True)
        return
    user_id = int(callback.data.rsplit(":",1)[-1])
    await _discount_customer_page(callback.message,user_id)
    await callback.answer()


@payg_router.callback_query(F.data == "paygadmin:discount:search")
async def payg_discount_search(callback: CallbackQuery, state: FSMContext):
    if not _discount_owner(callback.from_user.id):
        await callback.answer("غیرمجاز",show_alert=True)
        return
    await state.clear()
    await state.set_state(PaygAdminStates.discount_customer)
    await callback.message.edit_text(
        "شناسه عددی تلگرام مشتری را ارسال کنید.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="لغو",callback_data="paygadmin:discounts"),
        ]]),
    )
    await callback.answer()


@payg_router.message(PaygAdminStates.discount_customer, F.text)
async def payg_discount_search_value(message: Message, state: FSMContext):
    if not _discount_owner(message.from_user.id):
        return
    value = (message.text or "").strip()
    if not value.isdecimal() or int(value) <= 0:
        await message.answer("شناسه عددی معتبر وارد کنید.")
        return
    await state.clear()
    await _discount_customer_page(message,int(value))


@payg_router.callback_query(F.data.startswith("paygadmin:discount:account:"))
async def payg_discount_account(callback: CallbackQuery, state: FSMContext):
    if not _discount_owner(callback.from_user.id):
        await callback.answer("غیرمجاز",show_alert=True)
        return
    try:
        account_id = int(callback.data.rsplit(":",1)[-1])
        account = await payg_service.get_account(account_id)
        if not account:
            raise PaygError("حساب پیدا نشد")
        policy = await payg_service.get_customer_discount(account_id)
        import aiosqlite
        async with aiosqlite.connect(payg_service.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                "SELECT base_rate,last_error FROM payg_discount_targets WHERE account_id=?",
                (account_id,),
            ) as cur:
                target = await cur.fetchone()
        base = int(target["base_rate"]) if target else int(account["price_per_gb_toman"])
        rate = int(account["price_per_gb_toman"])
        balance = await payg_service.get_balance(int(account["user_id"]))
        accrued = await payg_service.get_accrued_target(account_id)
        debt = max(0,int(accrued)-int(account["charged_toman_total"]))
        import time
        now = int(time.time())
        status = "بدون تخفیف"
        if policy:
            status = ("زمان‌بندی‌شده" if now < int(policy["starts_at"])
                      else "منقضی" if policy["expires_at"] is not None and now >= int(policy["expires_at"])
                      else "فعال")
        if target and target["last_error"]:
            status = "در انتظار تطبیق Rebecca"
        desc = (f"{policy['mode']}: {policy['value']}" if policy else "ندارد")
        await state.clear()
        await callback.message.edit_text(
            f"🎯 <b>حساب #{account_id}</b> · کاربر <code>{account['user_id']}</code>\n"
            f"وضعیت: {escape(status)} · {escape(str(account['status']))}\n"
            f"نرخ عمومی: {_money(base)}\nنرخ مؤثر: {_money(rate)}\n"
            f"سیاست: {escape(desc)}\n"
            f"موجودی: {_money(balance)} · بدهی: {_money(debt)}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✏️ ثبت/ویرایش تخفیف",callback_data=f"paygadmin:discount:edit:{account_id}")],
                [InlineKeyboardButton(text="🗑 لغو تخفیف",callback_data=f"paygadmin:discount:cancel:{account_id}")],
                [InlineKeyboardButton(text="📜 تاریخچه",callback_data=f"paygadmin:discount:history:{account_id}")],
                [InlineKeyboardButton(text="⚖️ بررسی مصرف مبهم",callback_data=f"paygadmin:discount:review:{account_id}")],
                [InlineKeyboardButton(text="🔙 جست‌وجو",callback_data="paygadmin:discounts")],
            ]),
        )
    except (ValueError,PaygError) as exc:
        await callback.answer(str(exc)[:150],show_alert=True)
        return
    await callback.answer()


@payg_router.callback_query(F.data.startswith("paygadmin:discount:edit:"))
async def payg_discount_edit(callback: CallbackQuery,state: FSMContext):
    if not _discount_owner(callback.from_user.id):
        await callback.answer("غیرمجاز",show_alert=True)
        return
    account_id = int(callback.data.rsplit(":",1)[-1])
    if not await payg_service.get_account(account_id):
        await callback.answer("حساب پیدا نشد",show_alert=True)
        return
    await state.clear()
    await state.update_data(discount_account=account_id)
    await state.set_state(PaygAdminStates.discount_policy)
    await callback.message.edit_text(
        "نوع و مقدار و تاریخ شروع و پایان را ارسال کنید:\n"
        "<code>percent 20 1730000000 1740000000</code>\n"
        "<code>fixed 1500 now never</code>\n"
        "زمان‌ها Unix timestamp (ثانیه) هستند؛ never یعنی بدون انقضا.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="لغو",callback_data=f"paygadmin:discount:account:{account_id}"),
        ]]),
    )
    await callback.answer()


@payg_router.message(PaygAdminStates.discount_policy,F.text)
async def payg_discount_edit_value(message: Message,state: FSMContext):
    if not _discount_owner(message.from_user.id):
        return
    import time
    parts = (message.text or "").strip().split()
    if len(parts) != 4 or parts[0] not in {"fixed","percent"}:
        await message.answer("فرمت معتبر نیست. نمونه: <code>percent 20 now never</code>")
        return
    try:
        mode = parts[0]
        value = int(parts[1])
        start = int(time.time()) if parts[2] == "now" else int(parts[2])
        expiry = None if parts[3] == "never" else int(parts[3])
        data = await state.get_data()
        account_id = int(data["discount_account"])
        account = await payg_service.get_account(account_id)
        if not account:
            raise PaygError("حساب پیدا نشد")
        policy = await payg_service.get_customer_discount(account_id)
        import aiosqlite
        async with aiosqlite.connect(payg_service.db_path) as conn:
            async with conn.execute(
                "SELECT base_rate FROM payg_discount_targets WHERE account_id=?",
                (account_id,),
            ) as cur:
                row = await cur.fetchone()
        base = int(row[0]) if row else int(account["price_per_gb_toman"])
        effective = payg_service.discount_rate(base,mode,value)
        if start <= 0 or (expiry is not None and expiry <= start):
            raise PaygError("بازه زمانی نامعتبر است")
    except (ValueError,KeyError,PaygError) as exc:
        await message.answer(f"خطا: {escape(str(exc))}")
        return
    await state.update_data(discount_mode=mode,discount_value=value,
                            discount_start=start,discount_expiry=expiry,
                            discount_base=base)
    await state.set_state(None)
    await message.answer(
        f"⚠️ <b>تأیید تغییر نرخ</b>\nحساب #{account_id}\n"
        f"نرخ عمومی: {_money(base)}\nنرخ هدف: {_money(effective)}\n"
        f"شروع: {start} · پایان: {expiry or 'بدون انقضا'}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ تأیید",callback_data=f"paygadmin:discount:confirm:{account_id}")],
            [InlineKeyboardButton(text="❌ انصراف",callback_data=f"paygadmin:discount:account:{account_id}")],
        ]),
    )


@payg_router.callback_query(F.data.startswith("paygadmin:discount:confirm:"))
async def payg_discount_confirm(callback: CallbackQuery,state: FSMContext):
    if not _discount_owner(callback.from_user.id):
        await callback.answer("غیرمجاز",show_alert=True)
        return
    account_id = int(callback.data.rsplit(":",1)[-1])
    data = await state.get_data()
    if int(data.get("discount_account",0)) != account_id or "discount_mode" not in data:
        await callback.answer("درخواست منقضی شده؛ دوباره شروع کنید.",show_alert=True)
        return
    try:
        await payg_service.configure_customer_discount(
            account_id,callback.from_user.id,mode=data["discount_mode"],
            value=int(data["discount_value"]),base_rate=int(data["discount_base"]),
            starts_at=int(data["discount_start"]),expires_at=data["discount_expiry"],
        )
        result = await payg_service.reconcile_customer_discounts()
        outcome = result.get(account_id,"scheduled")
    except Exception as exc:
        await callback.answer(f"خطا: {str(exc)[:120]}",show_alert=True)
        return
    await state.clear()
    await callback.message.edit_text(
        f"سیاست ذخیره شد؛ وضعیت تطبیق: {escape(outcome)}",
        reply_markup=_discount_menu(),
    )
    await callback.answer()


@payg_router.callback_query(F.data.startswith("paygadmin:discount:cancel:"))
async def payg_discount_cancel_prompt(callback: CallbackQuery):
    if not _discount_owner(callback.from_user.id):
        await callback.answer("غیرمجاز",show_alert=True)
        return
    account_id = int(callback.data.rsplit(":",1)[-1])
    await callback.message.edit_text(
        f"لغو سیاست تخفیف حساب #{account_id} و درخواست بازگشت به نرخ عمومی؟",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ تأیید لغو",callback_data=f"paygadmin:discount:cancelconfirm:{account_id}")],
            [InlineKeyboardButton(text="🔙 انصراف",callback_data=f"paygadmin:discount:account:{account_id}")],
        ]),
    )
    await callback.answer()


@payg_router.callback_query(F.data.startswith("paygadmin:discount:cancelconfirm:"))
async def payg_discount_cancel_confirm(callback: CallbackQuery):
    if not _discount_owner(callback.from_user.id):
        await callback.answer("غیرمجاز",show_alert=True)
        return
    account_id = int(callback.data.rsplit(":",1)[-1])
    try:
        changed = await payg_service.cancel_customer_discount(account_id,callback.from_user.id)
        outcome = (await payg_service.reconcile_customer_discounts()).get(account_id,"unchanged")
    except Exception as exc:
        await callback.answer(str(exc)[:120],show_alert=True)
        return
    await callback.message.edit_text(
        f"لغو سیاست: {'ثبت شد' if changed else 'سیاست فعالی یافت نشد'}\n"
        f"وضعیت نرخ: {escape(outcome)}",
        reply_markup=_discount_menu(),
    )
    await callback.answer()


@payg_router.callback_query(F.data.startswith("paygadmin:discount:review:"))
async def payg_discount_review_prompt(callback: CallbackQuery,state: FSMContext):
    if not _discount_owner(callback.from_user.id):
        await callback.answer("غیرمجاز",show_alert=True)
        return
    account_id = int(callback.data.rsplit(":",1)[-1])
    import aiosqlite
    async with aiosqlite.connect(payg_service.db_path) as conn:
        async with conn.execute(
            "SELECT last_error FROM payg_discount_targets WHERE account_id=?",
            (account_id,),
        ) as cur:
            row = await cur.fetchone()
    if row is None or row[0] is None:
        await callback.answer("بازه مبهم ثبت‌شده‌ای وجود ندارد.",show_alert=True)
        return
    await state.clear()
    await state.update_data(discount_review_account=account_id)
    await state.set_state(PaygAdminStates.discount_review_reason)
    await callback.message.edit_text(
        "⚠️ بازه مصرف نامشخص است. تنها تصمیم محافظه‌کارانه بدون "
        "قیمت‌گذاری حدسی، صرف‌نظر کردن از شارژ مصرف نامشخص است.\n"
        "برای ثبت تصمیم حسابرسی‌شده، علت را حداقل در ۱۲ نویسه ارسال کنید.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="انصراف",callback_data=f"paygadmin:discount:account:{account_id}"),
        ]]),
    )
    await callback.answer()


@payg_router.message(PaygAdminStates.discount_review_reason,F.text)
async def payg_discount_review_reason(message: Message,state: FSMContext):
    if not _discount_owner(message.from_user.id):
        return
    reason = (message.text or "").strip()
    if len(reason) < 12:
        await message.answer("علت باید حداقل ۱۲ نویسه باشد.")
        return
    data = await state.get_data()
    account_id = int(data["discount_review_account"])
    await state.update_data(discount_review_reason=reason)
    await state.set_state(None)
    await message.answer(
        f"⚠️ تأیید نهایی صرف‌نظر از شارژ بازه مبهم حساب #{account_id}\n"
        f"علت: {escape(reason[:250])}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="تأیید نهایی",callback_data=f"paygadmin:discount:reviewconfirm:{account_id}")],
            [InlineKeyboardButton(text="انصراف",callback_data=f"paygadmin:discount:account:{account_id}")],
        ]),
    )


@payg_router.callback_query(F.data.startswith("paygadmin:discount:reviewconfirm:"))
async def payg_discount_review_confirm(callback: CallbackQuery,state: FSMContext):
    if not _discount_owner(callback.from_user.id):
        await callback.answer("غیرمجاز",show_alert=True)
        return
    account_id = int(callback.data.rsplit(":",1)[-1])
    data = await state.get_data()
    if int(data.get("discount_review_account",0)) != account_id or not data.get("discount_review_reason"):
        await callback.answer("درخواست تأیید منقضی شده است.",show_alert=True)
        return
    try:
        await payg_service.resolve_discount_boundary_without_charge(
            account_id,callback.from_user.id,reason=data["discount_review_reason"],
        )
    except Exception as exc:
        await callback.answer(f"تطبیق انجام نشد: {str(exc)[:100]}",show_alert=True)
        return
    await state.clear()
    await callback.message.edit_text(
        "تطبیق با صرف‌نظر از شارژ مصرف مبهم ثبت و حسابرسی شد.",
        reply_markup=_discount_menu(),
    )
    await callback.answer()


@payg_router.callback_query(F.data.startswith("paygadmin:discount:history:"))
async def payg_discount_history(callback: CallbackQuery):
    if not _discount_owner(callback.from_user.id):
        await callback.answer("غیرمجاز",show_alert=True)
        return
    import aiosqlite
    account_id = int(callback.data.rsplit(":",1)[-1])
    await payg_service.ensure_schema()
    async with aiosqlite.connect(payg_service.db_path) as conn:
        async with conn.execute(
            "SELECT actor_id,action,new_mode,new_value,created_at "
            "FROM payg_customer_discount_audit WHERE account_id=? "
            "ORDER BY id DESC LIMIT 12", (account_id,),
        ) as cur:
            rows = await cur.fetchall()
    lines = [f"📜 تاریخچه تخفیف حساب #{account_id}"]
    lines.extend(
        f"• {escape(str(r[4]))} · مدیر {r[0]} · {escape(str(r[1]))} · "
        f"{escape(str(r[2] or '-'))} {r[3] if r[3] is not None else '-'}"
        for r in rows
    )
    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🔙 حساب",callback_data=f"paygadmin:discount:account:{account_id}"),
        ]]),
    )
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
        topup = await payg_service.get_topup(topup_id)
        if not topup:
            await callback.answer("درخواست شارژ پیدا نشد.", show_alert=True)
            return

        user_id = int(topup["user_id"])
        if (
            str(topup.get("purpose") or "") == "initial"
            and await payg_kyc_gate.requires_kyc(user_id)
        ):
            await payg_kyc_gate.hold_after_payment(topup_id, callback.from_user.id)
            try:
                await callback.bot.send_message(
                    user_id,
                    "💰 <b>پرداخت اولیه PAYG تأیید شد.</b>\n\n"
                    "ساخت پنل تا تکمیل KYC متوقف است و مبلغ هنوز وارد کیف پول قابل‌مصرف نشده است.",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                        InlineKeyboardButton(text="🪪 شروع احراز هویت", callback_data="kyc:start"),
                        InlineKeyboardButton(text="📋 وضعیت KYC", callback_data="kyc:status"),
                    ]]),
                )
            except Exception:
                pass
            await group_ops_service.send_text(
                callback.bot,
                "payments",
                f"💰 PAYG <code>#{topup_id}</code> پرداخت شد · کاربر <code>{user_id}</code> · <b>منتظر KYC</b>",
            )
            try:
                await callback.message.edit_caption(
                    caption=(callback.message.caption or "") + "\n\n💰 پرداخت تأیید شد · 🪪 منتظر KYC",
                    reply_markup=None,
                )
            except Exception:
                try:
                    await callback.message.edit_reply_markup(reply_markup=None)
                except Exception:
                    pass
            await callback.answer("پرداخت تأیید شد؛ منتظر KYC", show_alert=True)
            return

        topup = await payg_service.approve_topup(topup_id, callback.from_user.id)
        continuation = await payg_service.post_topup_approval(topup_id)
        balance = await payg_service.get_balance(user_id)
        user_text = f"✅ پرداخت تأیید شد.\n💰 موجودی کیف پول: <b>{_money(balance)}</b>"
        if continuation.get("legacy_initial_needs_service"):
            user_text += "\n\nℹ️ برای فعال‌سازی، از فروشگاه PAYG یک سرویس Rebecca انتخاب کنید. اعتبار شما محفوظ است."
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
            await callback.bot.send_message(user_id, user_text)
        except Exception:
            pass
        try:
            await callback.message.edit_caption(
                caption=(callback.message.caption or "") + "\n\n✅ تأیید شد",
                reply_markup=None,
            )
        except Exception:
            try:
                await callback.message.edit_reply_markup(reply_markup=None)
            except Exception:
                pass
        await group_ops_service.audit(
            actor_id=callback.from_user.id,
            action="payg.topup.approve",
            subject_type="topup",
            subject_id=topup_id,
            new_value={"user_id": user_id, "amount_toman": int(topup["amount_toman"])},
        )
        await group_ops_service.send_text(
            callback.bot,
            "payments",
            f"✅ شارژ PAYG <code>#{topup_id}</code> برای کاربر <code>{user_id}</code> تأیید شد.\n"
            f"💵 {_money(int(topup['amount_toman']))}",
        )
        await callback.answer("شارژ تأیید شد ✅")
    except Exception as exc:
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
    await group_ops_service.audit(
        actor_id=callback.from_user.id,
        action="payg.topup.reject",
        subject_type="topup",
        subject_id=topup_id,
        new_value={"user_id": int(topup["user_id"]), "status": "rejected"},
    )
    await group_ops_service.send_text(
        callback.bot,
        "payments",
        f"❌ شارژ PAYG <code>#{topup_id}</code> کاربر <code>{int(topup['user_id'])}</code> رد شد.",
    )
    await callback.answer("رد شد")
