"""Customer wallet and PAYG dashboard; all reads scoped to Telegram user ID."""
from __future__ import annotations

from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from payg_customer_reporting import (
    account_for_customer, customer_accounts, ledger_page, usage_report, wallet_summary,
)
from payg_service import GIB, payg_service
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
    rows.append([_b("📦 پلن‌های کامل / تمدید","admin_renew")])
    rows.append([_b("🔙 کیف پول","wallet:home")])
    await message.edit_text(
        "📦 <b>سرویس‌های من · PAYG</b>\n\nحساب‌های اعتباری شما:"
        if accounts else "حساب PAYG فعالی ندارید.",
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
        rows.append([_b("💰 افزایش اعتبار","wallet:topup")])
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


@payg_customer_center_router.callback_query(F.data=="wallet:topup")
async def wallet_topup(callback:CallbackQuery,state:FSMContext):
    await state.clear()
    accounts=await customer_accounts(callback.from_user.id)
    rows=[
        [_b(f"⚡ شارژ حساب #{a['id']}",f"paygsvc:topup:a:{a['service_id']}:{a['id']}" )]
        for a in accounts if a.get("service_id") is not None
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
    lines=[
        "📊 <b>گزارش مصرف PAYG</b>","",
        f"مصرف تجمعی: {int(account['cumulative_usage_bytes'])/GIB:.3f} GB",
        f"آخرین شمارنده ثبت‌شده: {int(account['last_raw_usage_bytes'])/GIB:.3f} GB",
        f"هزینه تاریخی: {_money(report['accrued']) if report['accrued'] is not None else 'نیازمند بررسی'}",
        f"برداشت از کیف پول: {_money(account['charged_toman_total'])}",
        "مصرف روزانه/هفتگی: تنها Snapshotهای ثبت‌شده قابل گزارش‌اند.",
        "تاریخچه Rebecca پیش از ثبت Snapshot در دسترس نیست.",
        "",
        "آخرین رخدادهای مصرف:",
    ]
    for event in report["audit"][:8]:
        lines.append(
            f"• #{event['id']} {escape(str(event['created_at']))} "
            f"{escape(str(event['event_type']))} · {_money(event['charged_toman'])}"
        )
    if not report["audit"]:
        lines.append("رخداد تاریخی قابل نمایش ثبت نشده است.")
    if report["rates"]:
        lines.append("\nتغییرات نرخ:")
        for row in report["rates"][:5]:
            lines.append(
                f"• {escape(str(row['created_at']))}: "
                f"{int(row['previous_rate']):,} → {int(row['new_rate']):,} ت/GB"
            )
    await callback.message.edit_text("\n".join(lines),reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [_b("🧾 گردش مالی","wallet:ledger:all:0")],
        [_b("🔙 داشبورد",f"paygcustomer:dashboard:{account_id}")],
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
