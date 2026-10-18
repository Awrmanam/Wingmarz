from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import time

from database import db
from group_ops_service import group_ops_service
from rebecca_api import RebeccaAPIError, rebecca_api


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
    """Apply one already-paid renewal order against Rebecca and local mirror.

    This function intentionally never handles payment verification. It may only
    be called from the existing order approval path after an operator has approved
    the payment (or, later, a bank adapter has produced a verified match).
    """
    order_id = int(order.get("id") or 0)
    target_admin_id = int(order.get("target_admin_id") or 0)
    if not order_id or not target_admin_id:
        raise RebeccaRenewalError("سفارش تمدید یا پنل مقصد نامعتبر است.")

    admin = await db.get_admin_by_id(target_admin_id)
    if not admin or not admin.marzban_username:
        raise RebeccaRenewalError("پنل مقصد تمدید یافت نشد.")

    remote = await rebecca_api.find_admin(str(admin.marzban_username))
    if not remote:
        raise RebeccaRenewalError("ادمین در Rebecca پیدا نشد.")

    now = int(time.time())
    apply_as_reset = bool(order.get("apply_as_reset"))

    if apply_as_reset:
        plan_id = int(order.get("plan_id") or 0)
        plan = await db.get_plan_by_id(plan_id) if plan_id else None
        if not plan:
            raise RebeccaRenewalError("پلن انتخابی برای تمدید کامل پیدا نشد.")

        new_data_limit = (
            int(plan.traffic_limit_bytes)
            if plan.traffic_limit_bytes is not None
            else None
        )
        new_expire = (
            now + int(plan.time_limit_seconds)
            if plan.time_limit_seconds is not None
            else None
        )
        new_users_limit = int(plan.max_users) if plan.max_users is not None else None

        await rebecca_api.update_admin_fields(
            str(admin.marzban_username),
            {
                "data_limit": new_data_limit,
                "expire": new_expire,
                "users_limit": new_users_limit,
            },
        )
        # Full renewal semantically starts a fresh allowance.
        await rebecca_api.reset_admin_usage(str(admin.marzban_username))

        local_fields = {
            "max_total_traffic": int(new_data_limit or 0),
            "max_total_time": int(plan.time_limit_seconds or 0),
            "max_users": int(new_users_limit or 1_000_000),
            "validity_days": (
                max(1, int(plan.time_limit_seconds) // 86400)
                if plan.time_limit_seconds is not None
                else 36500
            ),
            "origin_plan_id": int(plan.id),
            "created_at": datetime.utcnow(),
            "traffic_cumulative_bytes": 0,
            "traffic_last_raw_bytes": 0,
        }
        await db.update_admin(int(admin.id), **local_fields)

        message = (
            f"✅ تمدید کامل Rebecca اعمال شد.\n"
            f"📦 پلن: {plan.name}"
        )
        changes = {
            "data_limit": new_data_limit,
            "expire": new_expire,
            "users_limit": new_users_limit,
            "usage_reset": True,
            "plan_id": int(plan.id),
        }
    else:
        delta_traffic = int(order.get("delta_traffic_bytes") or 0)
        delta_time = int(order.get("delta_time_seconds") or 0)
        delta_users = int(order.get("delta_users") or 0)
        if not any((delta_traffic, delta_time, delta_users)):
            raise RebeccaRenewalError("مقادیر تمدید خالی است.")

        payload: dict[str, int] = {}
        local_fields: dict[str, int] = {}
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

        await rebecca_api.update_admin_fields(str(admin.marzban_username), payload)
        if local_fields:
            await db.update_admin(int(admin.id), **local_fields)
        message = "\n".join(lines)
        changes = payload

    if not await db.update_order(order_id, status="approved", approved_by=int(actor_id)):
        raise RebeccaRenewalError("تمدید در Rebecca انجام شد اما ثبت وضعیت سفارش محلی ناموفق بود.")

    await group_ops_service.audit(
        actor_id=int(actor_id),
        action="order.renew.rebecca",
        subject_type="order",
        subject_id=order_id,
        old_value={"status": order.get("status")},
        new_value={"status": "approved", "admin_id": int(admin.id), **changes},
    )

    return RebeccaRenewalResult(
        admin_id=int(admin.id),
        user_id=int(admin.user_id),
        message=message,
    )
