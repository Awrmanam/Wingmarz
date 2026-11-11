from __future__ import annotations

import logging
from datetime import datetime, timedelta

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

import config
from panel_providers import get_panel_provider
from payg_service import payg_service
from payg_usage_alerts import payg_usage_alert_service
from payg_notification_service import payg_notification_service
from group_ops_service import group_ops_service


logger = logging.getLogger(__name__)
payg_entry_router = Router(name="payg_entry")
_payg_scheduler: AsyncIOScheduler | None = None


def _ns(*parts: str) -> str:
    return ":".join(str(part) for part in parts)


async def _render_provider_storefront(message: Message, source: str, provider_key: str) -> None:
    provider = get_panel_provider(provider_key)
    if provider is None:
        await message.edit_text("Provider نامعتبر است.")
        return
    if not provider.implemented:
        await message.edit_text(
            f"🧩 <b>{provider.label}</b>\n\nاتصال این Provider هنوز آماده نشده است.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🔙 بازگشت", callback_data=_ns("panelprov", "root", source))
            ]]),
        )
        return
    if provider.key == "rebecca":
        if config.PANEL_PROVIDER != "rebecca":
            await message.edit_text(
                "⚠️ صدور Rebecca در این نصب فعال نیست.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="🔙 بازگشت", callback_data=_ns("panelprov", "root", source))
                ]]),
            )
            return
        from handlers.payg_offers import render_purchase_method
        await render_purchase_method(message, source)
        return
    await message.edit_text(
        "این Provider هنوز برای فروش آماده نشده است.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🔙 بازگشت", callback_data=_ns("panelprov", "root", source))
        ]]),
    )


# Provider selection now opens a billing-first purchase wizard. Buyers choose
# PAYG vs fixed pricing before seeing services, so the two models never mix.
@payg_entry_router.callback_query(F.data.startswith("panelprov:select:"))
async def provider_service_storefront(callback: CallbackQuery, state: FSMContext):
    parts = (callback.data or "").split(":")
    if len(parts) != 4 or parts[2] not in {"a", "p"}:
        await callback.answer("نامعتبر", show_alert=True)
        return
    await state.clear()
    await _render_provider_storefront(callback.message, parts[2], parts[3])
    await callback.answer()


# Backwards-compatible fixed-plan callback kept for old messages/buttons.
@payg_entry_router.callback_query(F.data.startswith("payg:fixed:"))
async def fixed_purchase_selected(callback: CallbackQuery, state: FSMContext):
    parts = (callback.data or "").split(":")
    if len(parts) != 4 or parts[2] not in {"a", "p"} or parts[3] != "rebecca":
        await callback.answer("نامعتبر", show_alert=True)
        return
    await state.clear()
    from handlers.plan_storefront import _render_storefront
    await _render_storefront(callback.message, parts[2])
    await callback.answer()


@payg_entry_router.callback_query(F.data == "sudo_menu_sales")
async def sales_menu_with_payg(callback: CallbackQuery):
    if callback.from_user.id not in config.SUDO_ADMINS:
        return
    await callback.message.edit_text(
        "💳 فروش و مالی:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🛒 مدیریت فروش", callback_data="sales_manage")],
            [
                InlineKeyboardButton(text=config.BUTTONS["sales_cards"], callback_data="sales_cards"),
                InlineKeyboardButton(text=config.BUTTONS["set_billing"], callback_data="set_billing"),
            ],
            [InlineKeyboardButton(
                text="⚡ تعرفه‌های PAYG بر اساس سرویس",
                callback_data="paygsvcadmin:home",
            )],
            [InlineKeyboardButton(text="🔌 سرویس‌های Rebecca", callback_data="rebecca_services")],
            [InlineKeyboardButton(text=config.BUTTONS["set_login_url"], callback_data="set_login_url")],
            [InlineKeyboardButton(text=config.BUTTONS["back"], callback_data="back_to_main")],
        ]),
    )
    await callback.answer()


@payg_entry_router.message(Command("payg"))
async def payg_admin_command(message: Message, state: FSMContext):
    if message.from_user.id not in config.SUDO_ADMINS:
        return
    await state.clear()
    from handlers.payg_offers import render_admin_home
    sent = await message.answer("⚡ در حال بارگذاری تعرفه‌های PAYG...")
    await render_admin_home(sent)


async def _send_usage_alerts(bot: Bot, account_id: int) -> None:
    alerts = await payg_usage_alert_service.pending_alerts(account_id)
    for alert in alerts:
        lines = [
            "📊 <b>گزارش مصرف PAYG</b>",
            "",
            f"مصرف شما از <b>{alert.milestone_gb} GB</b> عبور کرد.",
            f"🌐 مصرف ثبت‌شده فعلی: <b>{alert.current_usage_gb:.2f} GB</b>",
            f"💵 تعرفه: <b>{alert.rate_toman_per_gb:,} تومان / GB</b>",
            f"💸 هزینه هر 10GB: <b>{alert.interval_cost_toman:,} تومان</b>",
            f"🧾 مجموع کسرشده از کیف پول: <b>{alert.charged_total_toman:,} تومان</b>",
            f"💰 موجودی فعلی: <b>{alert.balance_toman:,} تومان</b>",
        ]
        if alert.outstanding_toman > 0:
            lines.append(f"⚠️ بدهی مصرف ثبت‌شده: <b>{alert.outstanding_toman:,} تومان</b>")
        else:
            lines.append("✅ هزینه مصرف از موجودی PAYG شما کسر شده است.")
        await bot.send_message(alert.user_id, "\n".join(lines))
        # Persist only after Telegram accepted the message. A failed send is
        # retried on the next billing tick instead of silently losing the alert.
        await payg_usage_alert_service.mark_sent(alert.account_id, alert.milestone_gb)


async def _billing_tick(bot: Bot) -> None:
    """Reconcile Rebecca first; then issue exactly-once-per-level notifications."""
    results = await payg_service.sync_all_accounts()
    seen_users: set[int] = set()
    for result in results:
        try:
            await _send_usage_alerts(bot, result.account_id)
        except Exception:
            logger.exception("Failed to send PAYG usage alert for account %s", result.account_id)

        # This runs for every successful reconciliation, including a panel
        # which was already suspended before this worker was restarted.
        try:
            if await payg_notification_service.should_notify_status(
                result.account_id, result.new_status
            ):
                if result.new_status == "suspended":
                    user_text = (
                        "🔴 <b>سرویس PAYG غیرفعال شد</b>\n\n"
                        f"🆔 شناسه حساب: <code>{result.account_id}</code>\n"
                        f"🧾 بدهی ثبت‌شده: <b>{result.outstanding_toman:,}</b> تومان\n"
                        f"💰 موجودی فعلی: <b>{result.balance_toman:,}</b> تومان\n\n"
                        "مصرف از Rebecca محاسبه و پنل در Rebecca غیرفعال شده است. "
                        "برای فعال‌سازی دوباره از بخش PAYG موجودی را افزایش دهید."
                    )
                else:
                    user_text = (
                        "🟢 <b>سرویس PAYG دوباره فعال شد</b>\n\n"
                        f"🆔 شناسه حساب: <code>{result.account_id}</code>\n"
                        f"💰 موجودی: <b>{result.balance_toman:,}</b> تومان\n\n"
                        "وضعیت پنل در Rebecca تأیید شد."
                    )
                await bot.send_message(int(result.user_id), user_text)
                await payg_notification_service.mark_status_notified(
                    result.account_id, result.new_status
                )
                await group_ops_service.send_text(
                    bot, "payments",
                    (
                        f"{'🔴 تعلیق' if result.new_status == 'suspended' else '🟢 فعال‌سازی مجدد'}"
                        f" PAYG · حساب <code>#{result.account_id}</code>\n"
                        f"🆔 Telegram ID: <code>{int(result.user_id)}</code>\n"
                        f"💰 موجودی: <b>{result.balance_toman:,}</b> تومان"
                    ),
                )
        except Exception:
            # A failed private send remains pending and is retried next tick.
            logger.exception("PAYG status notification failed for account %s", result.account_id)

        user_id = int(result.user_id)
        if user_id in seen_users:
            continue
        seen_users.add(user_id)
        try:
            balance = await payg_service.get_balance(user_id)
            threshold = await payg_notification_service.due_wallet_threshold(user_id, balance)
            if threshold is None:
                continue
            await bot.send_message(
                user_id,
                "⚠️ <b>هشدار کمبود موجودی PAYG</b>\n\n"
                f"💰 موجودی کیف پول: <b>{balance:,}</b> تومان\n"
                f"📉 آستانه هشدار: <b>{threshold:,}</b> تومان\n\n"
                "برای جلوگیری از توقف سرویس، کیف پول خود را شارژ کنید. "
                "هزینه مصرفی براساس ترافیک واقعی Rebecca محاسبه می‌شود.",
            )
            await payg_notification_service.mark_wallet_notified(user_id, balance)
        except Exception:
            logger.exception("PAYG wallet notification failed for Telegram ID %s", user_id)


async def _start_payg_scheduler(bot: Bot) -> None:
    global _payg_scheduler
    await payg_service.ensure_schema()
    await payg_usage_alert_service.ensure_schema()
    await payg_notification_service.ensure_schema()
    if _payg_scheduler and _payg_scheduler.running:
        return
    # Billing owns this single job; never duplicate it in MonitoringScheduler.
    # A one-minute ceiling minimizes overspending between usage snapshots.
    interval = max(30, min(int(getattr(config, "MONITORING_INTERVAL", 600)), 60))
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        _billing_tick,
        trigger=IntervalTrigger(
            seconds=interval,
            start_date=datetime.now() + timedelta(seconds=10),
        ),
        args=[bot],
        id="payg_billing",
        name="PAYG Wallet Billing",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    _payg_scheduler = scheduler
    logger.info("PAYG billing scheduler started (%ss)", interval)


async def _stop_payg_scheduler() -> None:
    global _payg_scheduler
    if _payg_scheduler and _payg_scheduler.running:
        _payg_scheduler.shutdown(wait=False)
    _payg_scheduler = None


payg_entry_router.startup.register(_start_payg_scheduler)
payg_entry_router.shutdown.register(_stop_payg_scheduler)
