"""Customer wallet and PAYG dashboard; all reads scoped to Telegram user ID."""
from __future__ import annotations

from html import escape

from aiogram import F, Router
from aiogram.filters import BaseFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from payg_customer_reporting import (
    account_for_customer, customer_accounts, historical_payg_admin_ids,
    ledger_page, usage_report, wallet_summary,
)
from payg_service import GIB, payg_service
from database import db
from rebecca_catalog import get_service_by_rebecca_id


payg_customer_center_router=Router(name="payg_customer_center")


def _b(label: str, target: str):
    return InlineKeyboardButton(text=label,callback_data=target)


def _money(value):
    return f"{int(value):,} تومان"


async def render_wallet(message,user_id: int):
    data=await wallet_summary(user_id)
    lines=[
        "💰 <b>کیف پول من</b>","",
        f"موجودی قابل استفاده: <b>{_money(data['balance'])}</b>",
        (f"بدهی PAYG: <b>{_money(data['debt'])}</b>" if data["debt_complete"]
         else "بدهی PAYG: ⚠️ نیازمند بررسی حسابداری تاریخی"),
        f"مجموع شارژ موفق: <b>{_money(data['topups'])}</b>",
        f"مجموع برداشت مصرف: <b>{_money(data['usage'])}</b>",
    ]
    last=data["last_debit"]
    lines.append(
        f"آخرین برداشت: <b>{_money(-int(last['amount_toman']))}</b> · {escape(str(last['created_at']))}"
        if last else "آخرین برداشت: ثبت نشده"
    )
    lines.append(f"آخرین ثبت مالی: {escape(str(data['last_update'] or 'ثبت نشده'))}")
    await message.edit_text("\n".join(lines),reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [_b("➕ افزایش اعتبار","wallet:topup")],
        [_b("🧾 گردش مالی","wallet:ledger:all:0")],
        [_b("⚡ حساب‌های PAYG","wallet:accounts")],
        [_b("🔙 صفحه اصلی","back_to_admin_main")],
    ]))


async def render_accounts(message,user_id: int):
    accounts=await customer_accounts(user_id)
    rows=[]
    for account in accounts:
        service_id=account.get("service_id")
        service=await get_service_by_rebecca_id(int(service_id)) if service_id is not None else None
        label=service.display_name if service else f"حساب #{account['id']}"
        rows.append([_b(f"⚡ PAYG · {label}",f"paygcustomer:dashboard:{account['id']}")])
    # Migrated PAYG accounts are retired, not eligible for FIXED renewal.
    linked={int(a["admin_id"]) for a in accounts}
    linked.update(await historical_payg_admin_ids(user_id))
    for admin in await db.get_admins_for_user(int(user_id)):
        if int(admin.id) not in linked:
            # Historical PAYG accounts can lack the service mapping; do not
            # accidentally advertise their fixed-plan renewal buttons.
            if await payg_service.get_admin_account(int(admin.id)):
                continue
            name=escape(str(getattr(admin,"admin_name",None) or f"پنل {admin.id}"))
            rows.append([_b(f"📦 FIXED · {name}",f"admin_renew_panel_{int(admin.id)}")])
    rows.append([_b("🔙 کیف پول","wallet:home")])
    await message.edit_text(
        "📦 <b>سرویس‌های من</b>\n\nحساب‌های PAYG و پلن‌های کامل شما:"
        if len(rows)>1 else "هنوز سرویسی برای نمایش ندارید.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


async def render_payg_dashboard(message,user_id: int,account_id: int):
    report=await usage_report(user_id,account_id)
    if not report:
        await message.edit_text("این حساب PAYG متعلق به شما نیست.")
        return
    account=report["account"]
    service_id=account.get("service_id")
    service=await get_service_by_rebecca_id(int(service_id)) if service_id is not None else None
    title=escape(service.display_name if service else f"حساب {account_id}")
    balance=await payg_service.get_balance(user_id)
    due=report["accrued"]
    debt=(max(0,int(due)-int(account["charged_toman_total"])) if due is not None else None)
    discount=report["discount"]
    lines=[
        f"⚡ <b>پنل اعتباری PAYG — {title}</b>","",
        f"وضعیت: <b>{escape(str(account['status']))}</b>",
        f"نرخ فعلی ثبت‌شده: <b>{_money(account['price_per_gb_toman'])}/GB</b>",
        f"موجودی کیف پول: <b>{_money(balance)}</b>",
        f"مصرف تجمعی: <b>{int(account['cumulative_usage_bytes'])/GIB:.3f} GB</b>",
        f"هزینه واقعی ثبت‌شده: <b>{_money(due) if due is not None else 'نیازمند بررسی'}</b>",
        f"برداشت ثبت‌شده: <b>{_money(account['charged_toman_total'])}</b>",
        f"بدهی: <b>{_money(debt) if debt is not None else 'نامشخص'}</b>",
        f"آخرین ثبت حساب: {escape(str(account.get('updated_at') or 'نامشخص'))}",
        "آخرین Sync مستقل Rebecca: در رکورد فعلی ذخیره نشده",
    ]
    if discount:
        lines.append(f"تخفیف اختصاصی: {escape(str(discount['mode']))} · {int(discount['value'])}")
    if report["hold"]:
        lines.append("⚠️ تغییر نرخ/تخفیف در انتظار بررسی دستی؛ محاسبه مصرف متوقف است.")
    rows=[]
    if service_id is not None:
        rows.append([_b("💰 افزایش اعتبار",f"paygsvc:topup:a:{service_id}:{account_id}")])
    else:
        # Old provider-wide PAYG accounts may predate service mapping.
        # Their existing account-specific top-up route remains supported.
        rows.append([_b("💰 افزایش اعتبار",f"payg:topup:{account_id}")])
    rows.extend([
        [_b("📊 گزارش مصرف",f"paygcustomer:report:{account_id}"),
         _b("🧾 گردش مالی","wallet:ledger:all:0")],
        [_b("👥 کاربران پنل",f"users_panel_{account['admin_id']}"),
         _b("🔐 اطلاعات ورود",f"info_panel_{account['admin_id']}")],
    ])
    if service_id is not None:
        rows.append([_b("🔄 تبدیل به پلن کامل",f"paygsvc:migrate:a:{service_id}:{account_id}")])
    rows.append([_b("🔙 سرویس‌های من","wallet:accounts")])
    await message.edit_text("\n".join(lines),reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@payg_customer_center_router.callback_query(F.data=="wallet:home")
async def wallet_home(callback:CallbackQuery,state:FSMContext):
    await state.clear()
    await render_wallet(callback.message,callback.from_user.id)
    await callback.answer()


@payg_customer_center_router.callback_query(F.data=="wallet:accounts")
async def wallet_accounts(callback:CallbackQuery,state:FSMContext):
    await state.clear()
    await render_accounts(callback.message,callback.from_user.id)
    await callback.answer()


@payg_customer_center_router.callback_query(F.data=="wallet:reports")
async def wallet_reports(callback:CallbackQuery,state:FSMContext):
    await state.clear()
    accounts=await customer_accounts(callback.from_user.id)
    rows=[[_b(f"📊 PAYG · حساب #{a['id']}",f"paygcustomer:report:{a['id']}")]
          for a in accounts]
    rows.append([_b("🔙 صفحه اصلی","back_to_admin_main")])
    await callback.message.edit_text(
        "📊 <b>گزارش مصرف</b>\\n\\nسرویس اعتباری موردنظر را انتخاب کنید."
        if accounts else "هنوز حساب PAYG برای گزارش مصرف ندارید.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@payg_customer_center_router.callback_query(F.data=="wallet:topup")
async def wallet_topup(callback:CallbackQuery,state:FSMContext):
    await state.clear()
    accounts=await customer_accounts(callback.from_user.id)
    rows=[
        [_b(f"⚡ شارژ حساب #{a['id']}",
            f"paygsvc:topup:a:{a['service_id']}:{a['id']}"
            if a.get("service_id") is not None else f"payg:topup:{a['id']}")]
        for a in accounts
    ]
    rows.append([_b("🛒 خرید سرویس PAYG","buyflow:payg:a")])
    rows.append([_b("🔙 کیف پول","wallet:home")])
    await callback.message.edit_text(
        "💰 <b>افزایش اعتبار</b>\n\nسرویس PAYG موردنظر را انتخاب کنید. "
        "اعتبار به کیف پول مشترک موجود افزوده می‌شود.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@payg_customer_center_router.callback_query(F.data.startswith("paygcustomer:dashboard:"))
async def payg_dashboard(callback:CallbackQuery,state:FSMContext):
    try:
        account_id=int(callback.data.rsplit(":",1)[-1])
    except ValueError:
        await callback.answer("شناسه نامعتبر",show_alert=True)
        return
    await state.clear()
    await render_payg_dashboard(callback.message,callback.from_user.id,account_id)
    await callback.answer()


@payg_customer_center_router.callback_query(F.data.startswith("paygcustomer:report:"))
async def payg_report(callback:CallbackQuery):
    try:
        account_id=int(callback.data.rsplit(":",1)[-1])
    except ValueError:
        await callback.answer("شناسه نامعتبر",show_alert=True)
        return
    report=await usage_report(callback.from_user.id,account_id)
    if not report:
        await callback.answer("حساب متعلق به شما نیست",show_alert=True)
        return
    account=report["account"]
    balance=await payg_service.get_balance(callback.from_user.id)
    accrued=report["accrued"]
    charged=int(account["charged_toman_total"])
    lines=[
        "📊 <b>گزارش مصرف PAYG</b>","",
        f"📦 مصرف کل: <b>{int(account['cumulative_usage_bytes'])/GIB:.2f} GB</b>",
        f"💸 هزینه مصرف: <b>{_money(accrued) if accrued is not None else 'نیازمند بررسی'}</b>",
        f"💰 موجودی کیف پول: <b>{_money(balance)}</b>",
    ]
    if accrued is not None and int(accrued)>charged:
        lines.append(f"⚠️ بدهی ثبت‌شده: <b>{_money(int(accrued)-charged)}</b>")
    if report["hold"]:
        lines.append("⚠️ بخشی از محاسبه مصرف نیازمند بررسی است.")
    await callback.message.edit_text("\n".join(lines),reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [_b("📋 جزئیات بیشتر",f"paygcustomer:detail:{account_id}")],
        [_b("🧾 گردش مالی","wallet:ledger:all:0")],
        [_b("🔙 داشبورد",f"paygcustomer:dashboard:{account_id}")],
    ]))
    await callback.answer()


@payg_customer_center_router.callback_query(F.data.startswith("paygcustomer:detail:"))
async def payg_report_details(callback:CallbackQuery):
    """Keep minute-level billing events available on demand, not in the default report."""
    try:
        account_id=int(callback.data.rsplit(":",1)[-1])
    except ValueError:
        await callback.answer("شناسه نامعتبر",show_alert=True)
        return
    report=await usage_report(callback.from_user.id,account_id)
    if not report:
        await callback.answer("حساب متعلق به شما نیست",show_alert=True)
        return
    account=report["account"]
    lines=[
        "📋 <b>جزئیات مصرف PAYG</b>","",
        f"آخرین شمارنده Rebecca: {int(account['last_raw_usage_bytes'])/GIB:.3f} GB",
        f"برداشت ثبت‌شده: {_money(account['charged_toman_total'])}",
        "رویدادهای زیر، تسویه‌های دوره‌ای هستند و تراکنش خرید جداگانه نیستند.",
        "",
        "آخرین ۵ رویداد:",
    ]
    for event in report["audit"][:5]:
        lines.append(
            f"• {escape(str(event['created_at']))} · {_money(event['charged_toman'])}"
        )
    if not report["audit"]:
        lines.append("رویداد مصرفی ثبت نشده است.")
    if report["rates"]:
        lines.append("\nتغییرات اخیر نرخ:")
        for row in report["rates"][:5]:
            lines.append(
                f"• {escape(str(row['created_at']))}: "
                f"{int(row['previous_rate']):,} ← {int(row['new_rate']):,} ت/GB"
            )
    lines.append("\nگزارش روزانه/هفتگی تنها در صورت وجود داده تاریخی معتبر امکان‌پذیر است.")
    await callback.message.edit_text("\n".join(lines),reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [_b("🔙 گزارش خلاصه",f"paygcustomer:report:{account_id}")],
    ]))
    await callback.answer()


@payg_customer_center_router.callback_query(F.data.startswith("wallet:ledger:"))
async def wallet_ledger(callback:CallbackQuery):
    parts=(callback.data or "").split(":")
    if len(parts)!=4 or not parts[3].isdigit():
        await callback.answer("درخواست نامعتبر",show_alert=True)
        return
    category,page=parts[2],int(parts[3])
    try:
        rows,more=await ledger_page(callback.from_user.id,category,page)
    except ValueError:
        await callback.answer("درخواست نامعتبر",show_alert=True)
        return
    names={"all":"همه","topup":"شارژ","payg_usage":"مصرف","refund":"بازپرداخت",
           "adjustment":"اصلاح مالی","other":"سایر"}
    lines=[f"🧾 <b>گردش مالی · {names[category]}</b>",""]
    for row in rows:
        lines.append(
            f"#{row['id']} · {escape(str(row['created_at']))}\n"
            f"{escape(str(row['kind']))}: <b>{int(row['amount_toman']):+,} تومان</b>\n"
            f"مرجع: {escape(str(row['reference_type'] or '-'))} "
            f"#{escape(str(row['reference_id'] or '-'))} · "
            f"مانده پس از تراکنش: {_money(row['balance_after_toman'])}"
        )
    if not rows:
        lines.append("تراکنشی ثبت نشده است.")
    filters=[[_b(label,f"wallet:ledger:{key}:0")] for key,label in names.items()]
    nav=[]
    if page:
        nav.append(_b("◀ قبلی",f"wallet:ledger:{category}:{page-1}"))
    if more:
        nav.append(_b("بعدی ▶",f"wallet:ledger:{category}:{page+1}"))
    if nav:
        filters.append(nav)
    filters.append([_b("🔙 کیف پول","wallet:home")])
    await callback.message.edit_text("\n\n".join(lines),reply_markup=InlineKeyboardMarkup(inline_keyboard=filters))
    await callback.answer()


class LegacyFixedRenewalForPayg(BaseFilter):
    """Intercept stale fixed-plan buttons before any order-creating handler."""
    PREFIXES=(
        "admin_renew_panel_","admin_full_renew_","admin_combo_renew_",
        "admin_reset_renew_","admin_renew_traffic_","admin_renew_time_",
        "admin_renew_users_",
    )

    async def __call__(self,callback:CallbackQuery)->bool:
        data=str(callback.data or "")
        if not data.startswith(self.PREFIXES):
            return False
        try:
            # Plan selectors contain both the admin ID and a plan ID.
            admin_id=int(data.split("_")[-2] if "_plan_" in data else data.split("_")[-1])
        except (TypeError,ValueError):
            return False
        admin=await db.get_admin_by_id(admin_id)
        if not admin or int(admin.user_id)!=int(callback.from_user.id):
            return False
        account=await payg_service.get_admin_account(admin_id)
        if account is not None:
            return True
        # A stale Telegram button may still reference the retired PAYG admin
        # after migration. Such an ID must never reach fixed-plan order flow.
        return admin_id in await historical_payg_admin_ids(callback.from_user.id)


@payg_customer_center_router.callback_query(LegacyFixedRenewalForPayg())
async def redirect_legacy_fixed_renewal(callback:CallbackQuery,state:FSMContext):
    data=str(callback.data or "")
    admin_id=int(data.split("_")[-2] if "_plan_" in data else data.split("_")[-1])
    account=await payg_service.get_admin_account(admin_id)
    if account is None:
        if admin_id not in await historical_payg_admin_ids(callback.from_user.id):
            await callback.answer("حساب متعلق به شما نیست",show_alert=True)
            return
        await state.clear()
        await render_accounts(callback.message,callback.from_user.id)
        await callback.answer("پنل قدیمی PAYG تبدیل شده؛ پنل جدید را انتخاب کنید.",show_alert=True)
        return
    if int(account["user_id"])!=int(callback.from_user.id):
        await callback.answer("حساب متعلق به شما نیست",show_alert=True)
        return
    await state.clear()
    await render_payg_dashboard(callback.message,callback.from_user.id,int(account["id"]))
    await callback.answer()
