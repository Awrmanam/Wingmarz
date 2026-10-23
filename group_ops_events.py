from __future__ import annotations

from html import escape
import logging
from typing import Any

from group_ops_service import group_ops_service
from trust_service import trust_service


logger = logging.getLogger(__name__)


def _username(user: Any) -> str:
    value = getattr(user, "username", None)
    return f"@{escape(str(value))}" if value else "—"


async def mirror_order_receipt(
    bot,
    *,
    order: dict[str, Any],
    user: Any,
    file_id: str,
    reply_markup=None,
    label: str = "سفارش",
) -> bool:
    """Mirror one receipt to the dedicated Receipts topic.

    Payment state and unverified identity alerts are emitted as separate text
    events, so receipt photos never get mixed with KYC documents.
    """
    order_id = int(order.get("id") or 0)
    user_id = int(order.get("user_id") or getattr(user, "id", 0) or 0)
    trusted = await trust_service.is_identity_approved(user_id)
    auto_enabled = await trust_service.auto_approve_enabled(user_id)
    verification = await trust_service.payment_verification(order_id)
    bank_state = str((verification or {}).get("state") or "unverified")
    plan_name = escape(str(order.get("plan_name_snapshot") or label))
    amount = int(order.get("price_snapshot") or 0)

    caption = (
        f"🧾 <b>رسید {escape(label)} #{order_id}</b>\n\n"
        f"👤 <code>{user_id}</code> · {_username(user)}\n"
        f"📦 {plan_name}\n"
        f"💵 <b>{amount:,}</b> تومان\n"
        f"🪪 هویت: <b>{'تأیید' if trusted else 'تأییدنشده'}</b>\n"
        f"🤖 Auto Approve: <b>{'مجاز' if auto_enabled else 'خاموش'}</b>\n"
        f"🏦 Bank Match: <b>{escape(bank_state)}</b>"
    )
    photo_ok = await group_ops_service.send_photo(
        bot,
        "receipts",
        file_id,
        caption=caption,
        reply_markup=reply_markup,
    )

    await group_ops_service.send_text(
        bot,
        "payments",
        (
            f"💳 <b>رسید جدید ثبت شد</b>\n"
            f"🧾 سفارش: <code>#{order_id}</code>\n"
            f"👤 <code>{user_id}</code>\n"
            f"💵 <b>{amount:,}</b> تومان\n"
            f"🪪 {'تأیید' if trusted else 'نیازمند KYC/Trust'}"
        ),
    )

    if not trusted:
        await group_ops_service.send_text(
            bot,
            "unverified",
            (
                "🟡 <b>پرداخت کاربر تأییدنشده</b>\n\n"
                f"👤 <code>{user_id}</code> · {_username(user)}\n"
                f"🧾 سفارش: <code>#{order_id}</code>\n"
                "رسید در تاپیک «رسیدهای پرداخت» ثبت شده است."
            ),
        )

    if not photo_ok:
        logger.warning("Order receipt #%s could not be mirrored to Group Ops", order_id)
    return photo_ok


async def mirror_payg_receipt(
    bot,
    *,
    topup: dict[str, Any],
    topup_id: int,
    user: Any,
    file_id: str,
    reply_markup=None,
) -> bool:
    user_id = int(topup.get("user_id") or getattr(user, "id", 0) or 0)
    trusted = await trust_service.is_identity_approved(user_id)
    auto_enabled = await trust_service.auto_approve_enabled(user_id)
    amount = int(topup.get("amount_toman") or 0)

    caption = (
        f"🧾 <b>رسید شارژ PAYG #{int(topup_id)}</b>\n\n"
        f"👤 <code>{user_id}</code> · {_username(user)}\n"
        f"💵 <b>{amount:,}</b> تومان\n"
        f"نوع: {escape(str(topup.get('purpose') or 'wallet'))}\n"
        f"🪪 هویت: <b>{'تأیید' if trusted else 'تأییدنشده'}</b>\n"
        f"🤖 Auto Approve: <b>{'مجاز' if auto_enabled else 'خاموش'}</b>"
    )
    photo_ok = await group_ops_service.send_photo(
        bot,
        "receipts",
        file_id,
        caption=caption,
        reply_markup=reply_markup,
    )
    await group_ops_service.send_text(
        bot,
        "payments",
        (
            f"⚡ <b>شارژ PAYG جدید</b>\n"
            f"شناسه: <code>#{int(topup_id)}</code>\n"
            f"👤 <code>{user_id}</code>\n"
            f"💵 <b>{amount:,}</b> تومان"
        ),
    )
    if not trusted:
        await group_ops_service.send_text(
            bot,
            "unverified",
            (
                "🟡 <b>شارژ PAYG کاربر تأییدنشده</b>\n"
                f"👤 <code>{user_id}</code> · {_username(user)}\n"
                f"رسید: <code>PAYG-{int(topup_id)}</code>"
            ),
        )
    return photo_ok
