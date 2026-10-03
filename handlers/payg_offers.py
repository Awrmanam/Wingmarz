from __future__ import annotations

from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import config
from database import db
from payg_offers_service import PaygOfferError, payg_offer_service
from payg_service import PaygError, PaygInsufficientFunds, payg_service
from rebecca_catalog import get_service, get_service_by_rebecca_id, list_services
from service_marketplace_service import service_marketplace_service
from utils.rebecca import credential_message, parse_service_ids


payg_offers_router = Router(name="payg_service_offers")


class ServicePaygUserStates(StatesGroup):
    amount = State()
    receipt = State()


class ServicePaygAdminStates(StatesGroup):
    rate = State()
    minimum = State()
    max_users = State()


def _money(value: int) -> str:
    return f"{int(value):,} تومان"


def _status_fa(status: str) -> str:
    return {
        "active": "🟢 فعال",
        "suspended": "🔴 متوقف به‌دلیل اتمام اعتبار",
        "migrated": "🔁 تبدیل‌شده به پلن کامل",
    }.get(str(status), str(status))


def _home(source: str) -> str:
    return "back_to_admin_main" if source == "a" else "public_back_main"


async def render_service_storefront(message: Message, source: str) -> None:
    """Render one unified Rebecca service list for PAYG and fixed purchases."""
    catalog = await list_services(enabled_only=True)
    offers = {
        int(item["service_id"]): item
        for item in await payg_offer_service.list_offers(enabled_only=True)
    }
    fixed = {
        int(service.rebecca_service_id): int(count)
        for service, count in await service_marketplace_service.sellable_services()
    }

    rows: list[list[InlineKeyboardButton]] = []
    lines = [
        "🔌 <b>سرویس موردنظر را انتخاب کنید</b>",
        "",
        "برای هر سرویس، PAYG و پلن‌های کامل/ماهانه جداگانه نمایش داده می‌شوند.",
    ]
    for service in catalog:
        service_id = int(service.rebecca_service_id)
        offer = offers.get(service_id)
        fixed_count = fixed.get(service_id, 0)
        if not offer and fixed_count <= 0:
            continue
        badges = []
        if offer:
            badges.append(f"⚡ {int(offer['price_per_gb_toman']):,}/GB")
        if fixed_count:
            badges.append(f"📦 {fixed_count} پلن")
        suffix = " · ".join(badges)
        rows.append([
            InlineKeyboardButton(
                text=f"{service.display_name} · {suffix}",
                callback_data=f"paygsvc:view:{source}:{int(service.id)}",
            )
        ])
    if not rows:
        lines.extend([
            "",
            "در حال حاضر برای هیچ سرویس Rebecca محصول فعالی تعریف نشده است.",
        ])
    rows.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data=f"panelprov:root:{source}")])
    await message.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


async def _render_service_detail(message: Message, source: str, catalog_id: int) -> None:
    service = await get_service(int(catalog_id))
    if not service or not service.is_enabled:
        await message.edit_text("❌ این سرویس دیگر فعال نیست.")
        return
    service_id = int(service.rebecca_service_id)
    offer = await payg_offer_service.get_offer(service_id)
    plans = await service_marketplace_service.plans_for_service(service_id)
    lines = [
        f"🔌 <b>{escape(service.display_name)}</b>",
        "",
        "روش خرید را انتخاب کنید:",
    ]
    rows: list[list[InlineKeyboardButton]] = []
    if offer and int(offer["enabled"]) and int(offer["price_per_gb_toman"]) > 0:
        lines.append(f"⚡ PAYG: <b>{_money(int(offer['price_per_gb_toman']))} / GB</b>")
        rows.append([
            InlineKeyboardButton(
                text=f"⚡ پرداخت به‌ازای مصرف · {int(offer['price_per_gb_toman']):,} ت/GB",
                callback_data=f"paygsvc:offer:{source}:{service_id}",
            )
        ])
    if plans:
        lines.append(f"📦 پلن کامل/ماهانه: <b>{len(plans)} گزینه</b>")
        rows.append([
            InlineKeyboardButton(
                text="📦 پلن کامل / ماهانه",
                callback_data=f"paygsvc:fixed:{source}:{int(catalog_id)}",
            )
        ])
    if len(rows) == 0:
        lines.append("\nفعلاً روش خرید فعالی برای این سرویس وجود ندارد.")
    rows.append([InlineKeyboardButton(text="🔙 سرویس‌ها", callback_data=f"paygsvc:root:{source}")])
    await message.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@payg_offers_router.callback_query(F.data.startswith("paygsvc:root:"))
async def service_storefront_root(callback: CallbackQuery, state: FSMContext):
    source = (callback.data or "").rsplit(":", 1)[-1]
    if source not in {"a", "p"}:
        await callback.answer("نامعتبر", show_alert=True)
        return
    await state.clear()
    await render_service_storefront(callback.message, source)
    await callback.answer()


@payg_offers_router.callback_query(F.data.startswith("paygsvc:view:"))
async def service_storefront_detail(callback: CallbackQuery, state: FSMContext):
    parts = (callback.data or "").split(":")
    if len(parts) != 4 or parts[2] not in {"a", "p"}:
        await callback.answer("نامعتبر", show_alert=True)
        return
    try:
        catalog_id = int(parts[3])
    except ValueError:
        await callback.answer("نامعتبر", show_alert=True)
        return
    await state.clear()
    await _render_service_detail(callback.message, parts[2], catalog_id)
    await callback.answer()


@payg_offers_router.callback_query(F.data.startswith("paygsvc:fixed:"))
async def service_fixed_purchase(callback: CallbackQuery, state: FSMContext):
    parts = (callback.data or "").split(":")
    if len(parts) != 4 or parts[2] not in {"a", "p"}:
        await callback.answer("نامعتبر", show_alert=True)
        return
    try:
        catalog_id = int(parts[3])
    except ValueError:
        await callback.answer("نامعتبر", show_alert=True)
        return
    service = await get_service(catalog_id)
    if not service:
        await callback.answer("سرویس پیدا نشد.", show_alert=True)
        return
    plans = await service_marketplace_service.plans_for_service(int(service.rebecca_service_id))
    from handlers.service_marketplace import _render_plans
    await state.clear()
    await _render_plans(
        callback.message,
        source=parts[2],
        catalog_id=catalog_id,
        plans=plans,
        back_callback=f"paygsvc:view:{parts[2]}:{catalog_id}",
    )
    await callback.answer()


async def _render_offer(message: Message, user_id: int, source: str, service_id: int) -> None:
    service = await get_service_by_rebecca_id(int(service_id))
    snapshot = await payg_offer_service.snapshot(int(user_id), int(service_id))
    offer = snapshot["offer"]
    if not service or not offer or not int(offer["enabled"]):
        await message.edit_text(
            "⚡ PAYG این سرویس در حال حاضر فعال نیست.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🔙 بازگشت", callback_data=f"paygsvc:root:{source}")
            ]]),
        )
        return

    account = snapshot["account"]
    balance = int(snapshot["balance_toman"])
    rate = int(snapshot["rate_toman"])
    minimum = int(offer["min_topup_toman"])
    lines = [
        f"⚡ <b>PAYG · {escape(service.display_name)}</b>",
        "",
        f"💵 نرخ هر گیگ: <b>{_money(rate)}</b>",
        f"💰 موجودی کیف پول: <b>{_money(balance)}</b>",
        f"📦 معادل تقریبی موجودی: <b>{float(snapshot['equivalent_gb']):.2f} GB</b>",
    ]
    rows: list[list[InlineKeyboardButton]] = []
    if account:
        used_gb = int(account["cumulative_usage_bytes"] or 0) / (1024 ** 3)
        lines.extend([
            f"📊 مصرف ثبت‌شده: <b>{used_gb:.3f} GB</b>",
            f"🧾 هزینه کسرشده: <b>{_money(int(account['charged_toman_total']))}</b>",
            f"📌 وضعیت: <b>{_status_fa(str(account['status']))}</b>",
        ])
        outstanding = int(snapshot["outstanding_toman"])
        if outstanding:
            lines.append(f"⚠️ بدهی مصرف: <b>{_money(outstanding)}</b>")
        rows.append([
            InlineKeyboardButton(
                text="➕ افزایش اعتبار",
                callback_data=f"paygsvc:topup:{source}:{int(service_id)}:{int(account['id'])}",
            ),
            InlineKeyboardButton(
                text="📜 گردش کیف پول",
                callback_data=f"paygsvc:history:{source}:{int(service_id)}",
            ),
        ])
        rows.append([
            InlineKeyboardButton(
                text="🔄 تبدیل به پلن کامل",
                callback_data=f"paygsvc:migrate:{source}:{int(service_id)}:{int(account['id'])}",
            )
        ])
    else:
        lines.extend([
            "",
            f"حداقل شارژ اولیه: <b>{_money(minimum)}</b>",
            "پس از تأیید پرداخت، پنل همین سرویس به‌صورت خودکار ساخته می‌شود.",
        ])
        if balance >= minimum:
            rows.append([
                InlineKeyboardButton(
                    text="⚡ ساخت پنل با موجودی فعلی",
                    callback_data=f"paygsvc:create:{source}:{int(service_id)}",
                )
            ])
        rows.append([
            InlineKeyboardButton(
                text="💳 شارژ و شروع PAYG",
                callback_data=f"paygsvc:start:{source}:{int(service_id)}",
            )
        ])
    rows.append([
        InlineKeyboardButton(
            text="🔙 بازگشت",
            callback_data=f"paygsvc:view:{source}:{int(service.id)}",
        )
    ])
    await message.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@payg_offers_router.callback_query(F.data.startswith("paygsvc:offer:"))
async def service_payg_offer(callback: CallbackQuery, state: FSMContext):
    parts = (callback.data or "").split(":")
    if len(parts) != 4 or parts[2] not in {"a", "p"}:
        await callback.answer("نامعتبر", show_alert=True)
        return
    await state.clear()
    await _render_offer(callback.message, callback.from_user.id, parts[2], int(parts[3]))
    await callback.answer()


@payg_offers_router.callback_query(F.data.startswith("paygsvc:create:"))
async def service_payg_create(callback: CallbackQuery, state: FSMContext):
    parts = (callback.data or "").split(":")
    if len(parts) != 4 or parts[2] not in {"a", "p"}:
        await callback.answer("نامعتبر", show_alert=True)
        return
    source, service_id = parts[2], int(parts[3])
    try:
        account = await payg_offer_service.provision(callback.from_user.id, service_id)
        admin = await db.get_admin_by_id(int(account["admin_id"]))
        service = await get_service_by_rebecca_id(service_id)
        if not admin:
            raise PaygOfferError("local admin missing after provisioning")
        login_url = admin.login_url or config.REBECCA_LOGIN_URL or config.REBECCA_URL
        await callback.message.edit_text(
            credential_message(
                str(admin.marzban_username),
                str(admin.marzban_password),
                str(login_url),
                f"PAYG {service.display_name if service else service_id}",
            )
            + "\n\nاز این لحظه هزینه بر اساس مصرف واقعی از کیف پول کم می‌شود.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🏠 صفحه اصلی", callback_data=_home(source))
            ]]),
        )
    except PaygInsufficientFunds as exc:
        await callback.answer(f"برای شروع {_money(exc.shortfall_toman)} دیگر شارژ کنید.", show_alert=True)
        return
    except Exception as exc:
        await callback.answer(f"ساخت پنل ناموفق بود: {str(exc)[:120]}", show_alert=True)
        return
    await state.clear()
    await callback.answer("پنل PAYG ساخته شد ✅")


async def _begin_amount(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    source: str,
    service_id: int,
    account_id: int | None = None,
) -> None:
    offer = await payg_offer_service.get_offer(service_id)
    if not offer or not int(offer["enabled"]):
        await callback.answer("PAYG این سرویس فعال نیست.", show_alert=True)
        return
    await state.clear()
    await state.update_data(
        payg_service_id=int(service_id),
        payg_source=source,
        payg_account_id=account_id,
    )
    await state.set_state(ServicePaygUserStates.amount)
    await callback.message.edit_text(
        f"💳 مبلغ شارژ را به تومان ارسال کنید.\n\n"
        f"حداقل برای این سرویس: <b>{_money(int(offer['min_topup_toman']))}</b>"
    )
    await callback.answer()


@payg_offers_router.callback_query(F.data.startswith("paygsvc:start:"))
async def service_payg_start(callback: CallbackQuery, state: FSMContext):
    parts = (callback.data or "").split(":")
    if len(parts) != 4 or parts[2] not in {"a", "p"}:
        await callback.answer("نامعتبر", show_alert=True)
        return
    await _begin_amount(callback, state, source=parts[2], service_id=int(parts[3]))


@payg_offers_router.callback_query(F.data.startswith("paygsvc:topup:"))
async def service_payg_topup(callback: CallbackQuery, state: FSMContext):
    parts = (callback.data or "").split(":")
    if len(parts) != 5 or parts[2] not in {"a", "p"}:
        await callback.answer("نامعتبر", show_alert=True)
        return
    service_id, account_id = int(parts[3]), int(parts[4])
    account = await payg_offer_service.account_for_user_service(callback.from_user.id, service_id)
    if not account or int(account["id"]) != account_id:
        await callback.answer("حساب پیدا نشد.", show_alert=True)
        return
    await _begin_amount(
        callback, state, source=parts[2], service_id=service_id, account_id=account_id
    )


async def _send_payment(message: Message, topup_id: int, *, edit: bool) -> None:
    topup = await payg_service.get_topup(topup_id)
    context = await payg_offer_service.topup_context(topup_id)
    if not topup or not context:
        if edit:
            await message.edit_text("❌ درخواست شارژ پیدا نشد.")
        else:
            await message.answer("❌ درخواست شارژ پیدا نشد.")
        return
    service = await get_service_by_rebecca_id(int(context["service_id"]))
    cards = await db.get_cards(only_active=True)
    lines = [
        f"💳 <b>شارژ PAYG · {escape(service.display_name if service else str(context['service_id']))}</b>",
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
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="✅ پرداخت کردم؛ ارسال رسید",
            callback_data=f"paygsvc:markpaid:{int(topup_id)}",
        )],
        [InlineKeyboardButton(
            text="🔙 بازگشت",
            callback_data=f"paygsvc:offer:{context['source']}:{int(context['service_id'])}",
        )],
    ])
    if edit:
        await message.edit_text("\n".join(lines), reply_markup=kb)
    else:
        await message.answer("\n".join(lines), reply_markup=kb)


@payg_offers_router.message(ServicePaygUserStates.amount, F.text)
async def service_payg_amount(message: Message, state: FSMContext):
    raw = (message.text or "").replace(",", "").replace(" ", "").strip()
    if not raw.isdecimal():
        await message.answer("مبلغ را فقط به‌صورت عدد و به تومان ارسال کنید.")
        return
    data = await state.get_data()
    service_id = int(data.get("payg_service_id") or 0)
    offer = await payg_offer_service.get_offer(service_id)
    if not offer:
        await state.clear()
        await message.answer("این تعرفه دیگر وجود ندارد.")
        return
    amount = int(raw)
    minimum = int(offer["min_topup_toman"])
    if amount < minimum:
        await message.answer(f"حداقل مبلغ شارژ این سرویس {_money(minimum)} است.")
        return
    topup_id = await payg_offer_service.create_topup(
        message.from_user.id,
        service_id,
        amount,
        source=str(data.get("payg_source") or "p"),
        account_id=(int(data["payg_account_id"]) if data.get("payg_account_id") else None),
    )
    await state.clear()
    await _send_payment(message, topup_id, edit=False)


@payg_offers_router.callback_query(F.data.startswith("paygsvc:markpaid:"))
async def service_payg_markpaid(callback: CallbackQuery, state: FSMContext):
    try:
        topup_id = int((callback.data or "").rsplit(":", 1)[-1])
    except ValueError:
        await callback.answer("نامعتبر", show_alert=True)
        return
    topup = await payg_service.get_topup(topup_id)
    context = await payg_offer_service.topup_context(topup_id)
    if (
        not topup
        or not context
        or int(topup["user_id"]) != int(callback.from_user.id)
        or topup["status"] != "pending"
    ):
        await callback.answer("این درخواست قابل پرداخت نیست.", show_alert=True)
        return
    await state.clear()
    await state.update_data(payg_service_topup_id=topup_id)
    await state.set_state(ServicePaygUserStates.receipt)
    await callback.message.edit_text("🧾 عکس رسید پرداخت را همین‌جا ارسال کنید.")
    await callback.answer()


@payg_offers_router.message(ServicePaygUserStates.receipt)
async def service_payg_receipt(message: Message, state: FSMContext):
    data = await state.get_data()
    topup_id = int(data.get("payg_service_topup_id") or 0)
    if not topup_id:
        await state.clear()
        return
    if not message.photo:
        await message.answer("لطفاً رسید را به‌صورت عکس ارسال کنید.")
        return
    if not await payg_service.submit_topup_receipt(topup_id, message.from_user.id, message.photo[-1].file_id):
        await state.clear()
        await message.answer("این درخواست قبلاً بررسی شده یا معتبر نیست.")
        return
    topup = await payg_service.get_topup(topup_id)
    context = await payg_offer_service.topup_context(topup_id)
    service = await get_service_by_rebecca_id(int(context["service_id"])) if context else None
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ تأیید شارژ", callback_data=f"paygsvcadmin:approve:{topup_id}"),
        InlineKeyboardButton(text="❌ رد", callback_data=f"paygsvcadmin:reject:{topup_id}"),
    ]])
    caption = (
        f"🧾 شارژ PAYG #{topup_id}\n"
        f"سرویس: {escape(service.display_name if service else str(context['service_id']))}\n"
        f"کاربر: <code>{message.from_user.id}</code>\n"
        f"مبلغ: <b>{_money(int(topup['amount_toman']))}</b>"
    )
    for sudo_id in config.SUDO_ADMINS:
        try:
            await message.bot.send_photo(
                sudo_id,
                photo=message.photo[-1].file_id,
                caption=caption,
                reply_markup=kb,
            )
        except Exception:
            pass
    await state.clear()
    await message.answer("✅ رسید ارسال شد. پس از تأیید، اعتبار به کیف پول اضافه می‌شود.")


@payg_offers_router.callback_query(F.data.startswith("paygsvc:history:"))
async def service_payg_history(callback: CallbackQuery):
    parts = (callback.data or "").split(":")
    if len(parts) != 4:
        await callback.answer("نامعتبر", show_alert=True)
        return
    source, service_id = parts[2], int(parts[3])
    items = await payg_service.wallet_history(callback.from_user.id, 15)
    balance = await payg_service.get_balance(callback.from_user.id)
    lines = ["📜 <b>گردش کیف پول</b>", "", f"موجودی: <b>{_money(balance)}</b>", ""]
    labels = {
        "topup": "شارژ",
        "payg_usage": "مصرف PAYG",
        "fixed_plan_purchase": "خرید پلن کامل",
        "migration_refund": "بازگشت وجه",
    }
    for item in items:
        amount = int(item["amount_toman"])
        sign = "+" if amount > 0 else ""
        lines.append(f"• {labels.get(str(item['kind']), str(item['kind']))}: <b>{sign}{amount:,}</b> ت")
    if not items:
        lines.append("هنوز تراکنشی ثبت نشده است.")
    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🔙 بازگشت", callback_data=f"paygsvc:offer:{source}:{service_id}")
        ]]),
    )
    await callback.answer()


@payg_offers_router.callback_query(F.data.startswith("paygsvc:migrate:"))
async def service_payg_migrate(callback: CallbackQuery):
    parts = (callback.data or "").split(":")
    if len(parts) != 5:
        await callback.answer("نامعتبر", show_alert=True)
        return
    source, service_id, account_id = parts[2], int(parts[3]), int(parts[4])
    account = await payg_offer_service.account_for_user_service(callback.from_user.id, service_id)
    if not account or int(account["id"]) != account_id:
        await callback.answer("حساب پیدا نشد.", show_alert=True)
        return
    plans = []
    for plan in await payg_service.list_migration_plans():
        try:
            if service_id in parse_service_ids(str(getattr(plan, "rebecca_service_ids", "") or "")):
                plans.append(plan)
        except ValueError:
            continue
    if not plans:
        await callback.answer("برای همین سرویس هنوز پلن کامل تعریف نشده است.", show_alert=True)
        return
    balance = await payg_service.get_balance(callback.from_user.id)
    rows = [[InlineKeyboardButton(
        text=f"{plan.name} · {int(plan.price):,} ت",
        callback_data=f"payg:migrateplan:{account_id}:{int(plan.id)}",
    )] for plan in plans]
    rows.append([InlineKeyboardButton(
        text="🔙 بازگشت", callback_data=f"paygsvc:offer:{source}:{service_id}"
    )])
    await callback.message.edit_text(
        "🔄 <b>تبدیل PAYG به پلن کامل همین سرویس</b>\n\n"
        f"موجودی کیف پول: <b>{_money(balance)}</b>\n"
        "اگر موجودی کافی نباشد فقط مابه‌التفاوت را پرداخت می‌کنید.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


# ---------------------------------------------------------------------------
# Admin: one PAYG tariff per Rebecca service
# ---------------------------------------------------------------------------

async def render_admin_home(message: Message) -> None:
    services = await payg_offer_service.list_catalog_with_offers()
    lines = [
        "⚡ <b>تعرفه‌های PAYG بر اساس سرویس</b>",
        "",
        "هر سرویس Rebecca تعرفه PAYG مستقل دارد.",
        "Service ID را فقط در «سرویس‌های Rebecca» تعریف می‌کنید؛ اینجا فقط قیمت و وضعیت فروش را تنظیم کنید.",
    ]
    rows: list[list[InlineKeyboardButton]] = []
    for service, offer in services:
        if offer and int(offer["enabled"]):
            label = f"🟢 {service.display_name} · {int(offer['price_per_gb_toman']):,} ت/GB"
        elif offer:
            label = f"⚪ {service.display_name} · {int(offer['price_per_gb_toman']):,} ت/GB"
        else:
            label = f"⚪ {service.display_name} · تنظیم نشده"
        rows.append([
            InlineKeyboardButton(
                text=label,
                callback_data=f"paygsvcadmin:view:{int(service.rebecca_service_id)}",
            )
        ])
    if not services:
        lines.extend([
            "",
            "هنوز هیچ سرویس Rebecca ثبت نشده است. ابتدا سرویس را اضافه کنید.",
        ])
    rows.extend([
        [InlineKeyboardButton(text="🔌 سرویس‌های Rebecca", callback_data="rebecca_services")],
        [InlineKeyboardButton(text="📦 پلن‌های کامل/ماهانه", callback_data="sales_manage")],
        [InlineKeyboardButton(text="🔙 فروش و مالی", callback_data="sudo_menu_sales")],
    ])
    await message.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


async def _render_admin_service(message: Message, service_id: int) -> None:
    service = await get_service_by_rebecca_id(int(service_id))
    if not service:
        await message.edit_text("این سرویس در کاتالوگ Rebecca وجود ندارد.")
        return
    offer = await payg_offer_service.ensure_offer(int(service_id))
    plans = await service_marketplace_service.plans_for_service(int(service_id))
    status = "🟢 فعال" if int(offer["enabled"]) else "⚪ غیرفعال"
    text = (
        f"⚡ <b>PAYG · {escape(service.display_name)}</b>\n\n"
        f"🆔 Rebecca Service ID: <code>{int(service_id)}</code>\n"
        f"وضعیت فروش PAYG: <b>{status}</b>\n"
        f"💵 نرخ هر GB: <b>{_money(int(offer['price_per_gb_toman']))}</b>\n"
        f"💳 حداقل شارژ: <b>{_money(int(offer['min_topup_toman']))}</b>\n"
        f"👥 حد کاربر پنل: <b>{int(offer['max_users'])}</b>\n"
        f"📦 پلن کامل متصل به این سرویس: <b>{len(plans)}</b>"
    )
    rows = [
        [InlineKeyboardButton(
            text="⛔ غیرفعال کردن PAYG" if int(offer["enabled"]) else "✅ فعال کردن PAYG",
            callback_data=f"paygsvcadmin:toggle:{int(service_id)}",
        )],
        [
            InlineKeyboardButton(text="💵 نرخ هر گیگ", callback_data=f"paygsvcadmin:set:rate:{int(service_id)}"),
            InlineKeyboardButton(text="💳 حداقل شارژ", callback_data=f"paygsvcadmin:set:minimum:{int(service_id)}"),
        ],
        [InlineKeyboardButton(text="👥 حد کاربر", callback_data=f"paygsvcadmin:set:users:{int(service_id)}")],
        [InlineKeyboardButton(text="📦 مدیریت پلن‌های کامل", callback_data="sales_manage")],
        [InlineKeyboardButton(text="🔙 تعرفه‌های PAYG", callback_data="paygsvcadmin:home")],
    ]
    await message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@payg_offers_router.callback_query(F.data == "paygsvcadmin:home")
async def service_payg_admin_home(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in config.SUDO_ADMINS:
        await callback.answer("غیرمجاز", show_alert=True)
        return
    await state.clear()
    await render_admin_home(callback.message)
    await callback.answer()


@payg_offers_router.callback_query(F.data.startswith("paygsvcadmin:view:"))
async def service_payg_admin_view(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in config.SUDO_ADMINS:
        await callback.answer("غیرمجاز", show_alert=True)
        return
    service_id = int((callback.data or "").rsplit(":", 1)[-1])
    await state.clear()
    await _render_admin_service(callback.message, service_id)
    await callback.answer()


@payg_offers_router.callback_query(F.data.startswith("paygsvcadmin:toggle:"))
async def service_payg_admin_toggle(callback: CallbackQuery):
    if callback.from_user.id not in config.SUDO_ADMINS:
        await callback.answer("غیرمجاز", show_alert=True)
        return
    service_id = int((callback.data or "").rsplit(":", 1)[-1])
    offer = await payg_offer_service.ensure_offer(service_id)
    try:
        await payg_offer_service.update_offer(service_id, enabled=not bool(int(offer["enabled"])))
    except PaygError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await _render_admin_service(callback.message, service_id)
    await callback.answer("ذخیره شد ✅")


async def _begin_admin_setting(
    callback: CallbackQuery, state: FSMContext, target: State, service_id: int, prompt: str
) -> None:
    if callback.from_user.id not in config.SUDO_ADMINS:
        await callback.answer("غیرمجاز", show_alert=True)
        return
    await state.clear()
    await state.update_data(payg_admin_service_id=int(service_id))
    await state.set_state(target)
    await callback.message.edit_text(prompt)
    await callback.answer()


@payg_offers_router.callback_query(F.data.startswith("paygsvcadmin:set:rate:"))
async def service_payg_admin_set_rate(callback: CallbackQuery, state: FSMContext):
    service_id = int((callback.data or "").rsplit(":", 1)[-1])
    await _begin_admin_setting(
        callback, state, ServicePaygAdminStates.rate, service_id,
        "نرخ هر 1GB این سرویس را به تومان ارسال کنید:",
    )


@payg_offers_router.callback_query(F.data.startswith("paygsvcadmin:set:minimum:"))
async def service_payg_admin_set_min(callback: CallbackQuery, state: FSMContext):
    service_id = int((callback.data or "").rsplit(":", 1)[-1])
    await _begin_admin_setting(
        callback, state, ServicePaygAdminStates.minimum, service_id,
        "حداقل مبلغ شارژ این سرویس را به تومان ارسال کنید:",
    )


@payg_offers_router.callback_query(F.data.startswith("paygsvcadmin:set:users:"))
async def service_payg_admin_set_users(callback: CallbackQuery, state: FSMContext):
    service_id = int((callback.data or "").rsplit(":", 1)[-1])
    await _begin_admin_setting(
        callback, state, ServicePaygAdminStates.max_users, service_id,
        "حداکثر تعداد کاربر پنل PAYG این سرویس را ارسال کنید:",
    )


async def _save_admin_number(message: Message, state: FSMContext, field: str) -> None:
    if message.from_user.id not in config.SUDO_ADMINS:
        return
    raw = (message.text or "").replace(",", "").replace(" ", "").strip()
    if not raw.isdecimal() or int(raw) < 1:
        await message.answer("عدد معتبر و بزرگ‌تر از صفر ارسال کنید.")
        return
    data = await state.get_data()
    service_id = int(data.get("payg_admin_service_id") or 0)
    if not service_id:
        await state.clear()
        await message.answer("اطلاعات سرویس منقضی شده است؛ دوباره وارد تنظیمات PAYG شوید.")
        return
    await payg_offer_service.update_offer(service_id, **{field: int(raw)})
    await state.clear()
    sent = await message.answer("✅ ذخیره شد.")
    await _render_admin_service(sent, service_id)


@payg_offers_router.message(ServicePaygAdminStates.rate, F.text)
async def service_payg_admin_rate_value(message: Message, state: FSMContext):
    await _save_admin_number(message, state, "price_per_gb_toman")


@payg_offers_router.message(ServicePaygAdminStates.minimum, F.text)
async def service_payg_admin_min_value(message: Message, state: FSMContext):
    await _save_admin_number(message, state, "min_topup_toman")


@payg_offers_router.message(ServicePaygAdminStates.max_users, F.text)
async def service_payg_admin_users_value(message: Message, state: FSMContext):
    await _save_admin_number(message, state, "max_users")


@payg_offers_router.callback_query(F.data.startswith("paygsvcadmin:approve:"))
async def service_payg_admin_approve(callback: CallbackQuery):
    if callback.from_user.id not in config.SUDO_ADMINS:
        await callback.answer("غیرمجاز", show_alert=True)
        return
    topup_id = int((callback.data or "").rsplit(":", 1)[-1])
    try:
        result = await payg_offer_service.approve_topup(topup_id, callback.from_user.id)
        topup = result["topup"]
        user_id = int(topup["user_id"])
        balance = await payg_service.get_balance(user_id)
        if result.get("account"):
            account = result["account"]
            admin = await db.get_admin_by_id(int(account["admin_id"]))
            service = await get_service_by_rebecca_id(int(result["service_id"]))
            if admin:
                login_url = admin.login_url or config.REBECCA_LOGIN_URL or config.REBECCA_URL
                await callback.bot.send_message(
                    user_id,
                    "✅ <b>پرداخت تأیید شد و پنل PAYG ساخته شد.</b>\n\n"
                    + credential_message(
                        str(admin.marzban_username),
                        str(admin.marzban_password),
                        str(login_url),
                        f"PAYG {service.display_name if service else result['service_id']}",
                    )
                    + f"\n\n💰 موجودی کیف پول: <b>{_money(balance)}</b>",
                )
        else:
            await callback.bot.send_message(
                user_id,
                f"✅ شارژ PAYG تأیید شد.\n💰 موجودی کیف پول: <b>{_money(balance)}</b>",
            )
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        await callback.answer("تأیید شد ✅", show_alert=True)
    except Exception as exc:
        await callback.answer(f"خطا: {str(exc)[:140]}", show_alert=True)


@payg_offers_router.callback_query(F.data.startswith("paygsvcadmin:reject:"))
async def service_payg_admin_reject(callback: CallbackQuery):
    if callback.from_user.id not in config.SUDO_ADMINS:
        await callback.answer("غیرمجاز", show_alert=True)
        return
    topup_id = int((callback.data or "").rsplit(":", 1)[-1])
    topup = await payg_service.get_topup(topup_id)
    if not topup or not await payg_service.reject_topup(topup_id, callback.from_user.id):
        await callback.answer("این درخواست قابل رد نیست.", show_alert=True)
        return
    try:
        await callback.bot.send_message(int(topup["user_id"]), "❌ رسید شارژ PAYG شما رد شد.")
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await callback.answer("رد شد")
