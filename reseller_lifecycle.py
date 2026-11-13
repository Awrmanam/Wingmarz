"""Independent, read-only Rebecca reseller lifecycle facts.

A disabled provider account is not necessarily expired, financially suspended,
or out of traffic. Never write a status based on stale/ambiguous API responses.
"""
from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any


def _nonnegative(value: Any) -> int | None:
    try:
        if isinstance(value, bool):
            return None
        number=int(value)
    except (TypeError,ValueError):
        return None
    return number if number>=0 else None


@dataclass(frozen=True)
class ResellerLifecycle:
    active: bool
    expired: bool
    quota_exhausted: bool
    financial_hold: bool
    manual_hold: bool
    requires_review: bool
    provider_disabled: bool
    provider_missing: bool
    usage_unknown: bool

    def labels(self) -> tuple[str,...]:
        result=[]
        if self.active:
            result.append("🟢 فعال")
        if self.expired:
            result.append("⌛ منقضی")
        if self.quota_exhausted:
            result.append("📦 اتمام حجم")
        if self.financial_hold:
            result.append("💳 تعلیق مالی")
        if self.manual_hold:
            result.append("🔒 تعلیق دستی/غیرمالی")
        if self.provider_disabled and not (self.manual_hold or self.financial_hold):
            result.append("⛔ غیرفعال در Rebecca")
        if self.requires_review:
            result.append("⚠️ نیازمند بررسی")
        if self.usage_unknown:
            result.append("📊 مصرف نامشخص")
        return tuple(result) or ("⚪ وضعیت نامشخص",)


def classify_reseller(
    remote: dict[str,Any] | None, *,
    usage_bytes: int | None,
    financial_status: str | None = None,
    local_active: bool = True,
    local_reason: str | None = None,
    api_failed: bool = False,
    now: int | None = None,
) -> ResellerLifecycle:
    instant = int(now if now is not None else time.time())
    present = isinstance(remote,dict)
    remote_status = str((remote or {}).get("status") or "").strip().lower()
    valid_status = remote_status in {"active","disabled"}
    reason = str(local_reason or "").strip().lower()
    financial = financial_status=="suspended"
    manually_disabled = (
        (not bool(local_active) and
         reason not in {"payg balance exhausted"})
        or (remote_status=="disabled" and not financial)
    ) if present and valid_status else (
        not bool(local_active) and reason!="payg balance exhausted"
    )
    expire = _nonnegative((remote or {}).get("expire")) if present else None
    quota = _nonnegative((remote or {}).get("data_limit")) if present else None
    raw = _nonnegative(usage_bytes)
    expired = bool(expire and expire<=instant)
    exhausted = bool(quota and raw is not None and raw>=quota)
    unknown_usage = bool(quota and raw is None)
    needs_review = bool(
        api_failed or not present or not valid_status
        or (remote_status=="active" and not bool(local_active))
        or (remote_status=="disabled" and bool(local_active) and not financial)
        or unknown_usage
    )
    active = bool(
        present and valid_status and remote_status=="active"
        and bool(local_active) and not expired and not exhausted
        and not financial and not manually_disabled and not needs_review
    )
    return ResellerLifecycle(
        active=active,expired=expired,quota_exhausted=exhausted,
        financial_hold=financial,manual_hold=manually_disabled,
        requires_review=needs_review,
        provider_disabled=remote_status=="disabled" if present else False,
        provider_missing=not present and not api_failed,
        usage_unknown=unknown_usage,
    )
