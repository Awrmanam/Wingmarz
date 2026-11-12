from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import time

from database import db
from group_ops_service import group_ops_service
from rebecca_api import RebeccaAPIError, rebecca_api
from rebecca_renewal_journal import (
    RenewalJournalError, rebecca_renewal_journal,
)


class RebeccaRenewalError(RuntimeError):
    pass


@dataclass(frozen=True)
class RebeccaRenewalResult:
    admin_id: int
    user_id: int
    message: str


def _finite_int(value, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise RebeccaRenewalError(
            f"{field} فعلی در Rebecca نامحدود است؛ افزایش تدریجی روی مقدار نامحدود معنی ندارد."
        )
    return int(value)


async def apply_rebecca_renewal(order: dict, actor_id: int) -> RebeccaRenewalResult:
    """Apply one paid renewal once, with a durable no-repeat safety fence.

    Rebecca has no cross-system transaction with SQLite. On any ambiguous
    network or persistence failure, the order/panel is quarantined for human
    reconciliation rather than sending another increment/reset automatically.
    """
    order_id = int(order.get("id") or 0)
    target_admin_id = int(order.get("target_admin_id") or 0)
    user_id = int(order.get("user_id") or 0)
    if not order_id or not target_admin_id or user_id <= 0:
        raise RebeccaRenewalError("سفارش تمدید یا پنل مقصد نامعتبر است.")

    admin = await db.get_admin_by_id(target_admin_id)
    if not admin or not admin.marzban_username:
        raise RebeccaRenewalError("پنل مقصد تمدید یافت نشد.")
    if int(admin.user_id) != user_id:
        raise RebeccaRenewalError("پنل تمدید متعلق به صاحب این سفارش نیست.")

    username = str(admin.marzban_username)
    remote = await rebecca_api.find_admin(username)
    if not remote:
        raise RebeccaRenewalError("ادمین در Rebecca پیدا نشد.")
    if str(remote.get("username") or "") != username:
        raise RebeccaRenewalError("هویت پنل Rebecca با سفارش مطابقت ندارد.")

    now = int(time.time())
    apply_as_reset = bool(order.get("apply_as_reset"))
    reserved = False

    if apply_as_reset:
        plan_id = int(order.get("plan_id") or 0)
        plan = await db.get_plan_by_id(plan_id) if plan_id else None
        if not plan:
            raise RebeccaRenewalError("پلن انتخابی برای تمدید کامل پیدا نشد.")

        new_data_limit = (
            int(plan.traffic_limit_bytes)
            if plan.traffic_limit_bytes is not None else None
        )
        new_expire = (
            now + int(plan.time_limit_seconds)
            if plan.time_limit_seconds is not None else None
        )
        new_users_limit = int(plan.max_users) if plan.max_users is not None else None
        payload: dict = {
            "data_limit": new_data_limit,
            "expire": new_expire,
            "users_limit": new_users_limit,
        }
        # JSON-serializable stable timestamp; SQLite/Pydantic still parse it.
        local_fields = {
            "max_total_traffic": int(new_data_limit or 0),
            "max_total_time": int(plan.time_limit_seconds or 0),
            "max_users": int(new_users_limit if new_users_limit is not None else 1_000_000),
            "validity_days": (
                max(1, int(plan.time_limit_seconds) // 86400)
                if plan.time_limit_seconds is not None else 36500
            ),
            "origin_plan_id": int(plan.id),
            "created_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            "traffic_cumulative_bytes": 0,
            "traffic_last_raw_bytes": 0,
        }
        changes = {**payload, "usage_reset": True, "plan_id": int(plan.id)}
        message = f"✅ تمدید کامل Rebecca اعمال شد.\n📦 پلن: {plan.name}"
    else:
        delta_traffic = int(order.get("delta_traffic_bytes") or 0)
        delta_time = int(order.get("delta_time_seconds") or 0)
        delta_users = int(order.get("delta_users") or 0)
        if min(delta_traffic, delta_time, delta_users) < 0:
            raise RebeccaRenewalError("مقادیر تمدید نباید منفی باشند.")
        if not any((delta_traffic, delta_time, delta_users)):
            raise RebeccaRenewalError("مقادیر تمدید خالی است.")

        payload = {}
        local_fields = {}
        lines = ["✅ تمدید Rebecca اعمال شد."]

        if delta_traffic:
            current = _finite_int(remote.get("data_limit"), "سقف حجم")
            payload["data_limit"] = current + delta_traffic
            local_fields["max_total_traffic"] = payload["data_limit"]
            lines.append(f"📦 حجم: +{delta_traffic / 1024**3:g} GB")

        if delta_time:
            current_expire = _finite_int(remote.get("expire"), "تاریخ انقضا")
            payload["expire"] = max(now, current_expire) + delta_time
            local_fields["max_total_time"] = int(admin.max_total_time or 0) + delta_time
            local_fields["validity_days"] = max(
                1, int(admin.validity_days or 0) + max(1, delta_time // 86400)
            )
            lines.append(f"⏳ زمان: +{delta_time // 86400} روز")

        if delta_users:
            current_users = _finite_int(remote.get("users_limit"), "User Limit")
            payload["users_limit"] = current_users + delta_users
            local_fields["max_users"] = payload["users_limit"]
            lines.append(f"👥 کاربر: +{delta_users}")

        changes = dict(payload)
        message = "\n".join(lines)

    # The reservation is stored BEFORE touching Rebecca. It atomically fences
    # repeated order callbacks and different renewals of the same panel.
    try:
        await rebecca_renewal_journal.reserve(
            order_id, int(admin.id), int(actor_id)
        )
    except RenewalJournalError as exc:
        raise RebeccaRenewalError(str(exc)) from exc
    reserved = True

    try:
        # A second paid order may have fetched an old quota before the first
        # order finished. Re-read after reserving the panel, before mutation.
        fresh = await rebecca_api.find_admin(username)
        if not isinstance(fresh, dict) or any(
            fresh.get(key) != remote.get(key) for key in payload
        ):
            raise RebeccaRenewalError(
                "محدودیت پنل پس از محاسبه تمدید تغییر کرده؛ نیاز به بررسی دستی دارد."
            )
        await rebecca_api.update_admin_fields(username, payload)
        verified = await rebecca_api.find_admin(username)
        if not isinstance(verified, dict) or any(
            verified.get(key) != value for key, value in payload.items()
        ):
            raise RebeccaRenewalError(
                "تغییرات Rebecca قابل تأیید نیست و نیاز به بررسی دستی دارد."
            )
        if apply_as_reset:
            # This is a non-idempotent endpoint: it MUST NOT be retried if
            # its outcome becomes ambiguous. The durable fence prevents it.
            await rebecca_api.reset_admin_usage(username)

        await rebecca_renewal_journal.finalize(
            order_id, int(admin.id), int(actor_id), local_fields
        )
    except Exception as exc:
        if reserved:
            try:
                await rebecca_renewal_journal.needs_review(order_id, exc)
            except Exception:
                # Keep the reservation in 'processing' if even writing the
                # failure status is unavailable; still fail closed on retries.
                pass
        raise RebeccaRenewalError(
            "نتیجه تمدید Rebecca نامشخص یا ناقص است؛ برای جلوگیری از "
            "تمدید دوباره، سفارش متوقف شد و بررسی دستی لازم است."
        ) from exc

    # The financial/order transaction is already committed. Operational audit
    # delivery failure must not cause a second remote mutation on retry.
    try:
        await group_ops_service.audit(
            actor_id=int(actor_id),
            action="order.renew.rebecca",
            subject_type="order",
            subject_id=order_id,
            old_value={"status": order.get("status")},
            new_value={"status": "approved", "admin_id": int(admin.id), **changes},
        )
    except Exception:
        pass

    return RebeccaRenewalResult(
        admin_id=int(admin.id),
        user_id=int(admin.user_id),
        message=message,
    )
