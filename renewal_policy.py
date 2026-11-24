"""Per-product-family renewal controls with backward-compatible defaults.

Stored in the existing settings table; no changes to orders or wallet schema.
A mode of 'legacy' preserves previously issued panels' historic renewal flow.
All non-legacy options are deliberately opt-in by the shop owner.
"""
from __future__ import annotations

from dataclasses import dataclass
import json

from database import db
from product_catalog import product_catalog
from service_marketplace_service import service_marketplace_service


MODES = {
    "legacy": "حالت فعلی (سازگاری قدیمی)",
    "both": "افزایش حجم و زمان با هم (جمع با اعتبار قبلی)",
    "reset_time": "افزایش حجم + شروع دوباره زمان پلن",
    "traffic": "فقط افزایش حجم",
    "time": "فقط افزایش زمان",
    "choose": "انتخاب نوع تمدید توسط مشتری",
}
RATE_FIELDS = ("per_gb_toman", "per_30days_toman", "per_user_toman")


class RenewalPolicyError(ValueError):
    pass


@dataclass(frozen=True)
class RenewalPolicy:
    mode: str = "legacy"
    per_gb_toman: int | None = None
    per_30days_toman: int | None = None
    per_user_toman: int | None = None

    def rate_override(self, field: str) -> int | None:
        if field not in RATE_FIELDS:
            raise RenewalPolicyError("تعرفه ناشناخته است.")
        return getattr(self, field)

    def serialize(self) -> str:
        return json.dumps(
            {"mode": self.mode, **{field: getattr(self, field) for field in RATE_FIELDS}},
            separators=(",", ":"), ensure_ascii=False,
        )


def _setting_key(category_id: int) -> str:
    if int(category_id) <= 0:
        raise RenewalPolicyError("شناسه دسته نامعتبر است.")
    return f"renewal:category:{int(category_id)}"


async def get_policy(category_id: int) -> RenewalPolicy:
    raw = await db.get_setting(_setting_key(category_id))
    if raw is None:
        return RenewalPolicy()
    try:
        obj = json.loads(raw)
        if not isinstance(obj, dict) or obj.get("mode") not in MODES:
            raise ValueError("mode")
        values = {}
        for field in RATE_FIELDS:
            value = obj.get(field)
            if value is not None and (type(value) is not int or value < 0 or value > 10**12):
                raise ValueError(field)
            values[field] = value
        return RenewalPolicy(mode=obj["mode"], **values)
    except (TypeError, ValueError) as exc:
        raise RenewalPolicyError("تنظیم تمدید این پنل نامعتبر است؛ مدیر باید بررسی کند.") from exc


async def save_mode(category_id: int, mode: str) -> RenewalPolicy:
    if mode not in MODES:
        raise RenewalPolicyError("روش تمدید انتخاب‌شده معتبر نیست.")
    policy = await get_policy(category_id)
    new_policy = RenewalPolicy(mode=mode, **{field: getattr(policy, field) for field in RATE_FIELDS})
    if not await db.set_setting(_setting_key(category_id), new_policy.serialize()):
        raise RenewalPolicyError("ذخیره روش تمدید ناموفق بود.")
    return new_policy


async def save_rate(category_id: int, field: str, value: int | None) -> RenewalPolicy:
    if field not in RATE_FIELDS:
        raise RenewalPolicyError("نوع تعرفه معتبر نیست.")
    if value is not None and (type(value) is not int or value < 0 or value > 10**12):
        raise RenewalPolicyError("مبلغ نامعتبر است.")
    policy = await get_policy(category_id)
    data = {name: getattr(policy, name) for name in RATE_FIELDS}
    data[field] = value
    updated = RenewalPolicy(mode=policy.mode, **data)
    if not await db.set_setting(_setting_key(category_id), updated.serialize()):
        raise RenewalPolicyError("ذخیره تعرفه ناموفق بود.")
    return updated


def _rebecca_admin_service_id(remote: object) -> int | None:
    """Return one exact service assignment, never a name-based guess.

    Rebecca admin records may expose service_id, service_ids or a services
    array of ids/objects. Missing/malformed/multiple mappings are ambiguous,
    so no service-specific quote may be created from those records.
    """
    if not isinstance(remote, dict):
        return None
    values: set[int] = set()
    saw_mapping = False

    def read_id(value: object) -> int | None:
        if isinstance(value, dict):
            value = value.get("service_id", value.get("id"))
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            return None
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed > 0 else None

    for field in ("service_id", "service_ids", "services"):
        if field not in remote or remote[field] is None:
            continue
        saw_mapping = True
        raw = remote[field]
        items = raw if isinstance(raw, list) else [raw]
        if not items:
            return None
        for item in items:
            service_id = read_id(item)
            if service_id is None:
                return None
            values.add(service_id)
    return next(iter(values)) if saw_mapping and len(values) == 1 else None


async def _category_id_for_admin(admin) -> int | None:
    """Resolve a FIXED admin's category from exact Rebecca assignment.

    For manual admins, origin_plan_id is typically NULL; their live Rebecca
    service ID is the only authoritative product identity. Existing purchased
    admins with one explicitly mapped origin plan can still renew when Rebecca
    is temporarily offline, without picking a random multi-service category.
    """
    plan_id = int(getattr(admin, "origin_plan_id", 0) or 0)
    origin_id: int | None = None
    if plan_id > 0:
        plan = await db.get_plan_by_id(plan_id)
        if plan:
            origin_services = set(service_marketplace_service.plan_service_ids(plan))
            if len(origin_services) == 1:
                origin_id = next(iter(origin_services))

    service_id: int | None = None
    username = str(getattr(admin, "marzban_username", "") or "").strip()
    if username:
        from rebecca_api import RebeccaAPIError, rebecca_api
        try:
            remote = await rebecca_api.find_admin(username)
        except RebeccaAPIError:
            remote = None
        if (isinstance(remote, dict)
                and str(remote.get("username", "")) == username):
            service_id = _rebecca_admin_service_id(remote)

    # A purchased plan can supply one trusted local service ID when Rebecca
    # has no usable assignment; a manually added admin cannot.
    resolved_id = service_id if service_id is not None else origin_id
    if resolved_id is None:
        return None
    category = await product_catalog.get_category_by_service(resolved_id)
    return int(category.id) if category else None


async def policy_for_admin(admin) -> tuple[int | None, RenewalPolicy]:
    category_id = await _category_id_for_admin(admin)
    if category_id is None:
        return None, RenewalPolicy()
    return category_id, await get_policy(category_id)


async def rates_for_admin(admin, *, policy: RenewalPolicy | None = None) -> dict[str, int]:
    base = await db.get_billing_rates()
    if policy is None:
        _, policy = await policy_for_admin(admin)
    return {
        field: int(getattr(policy, field) if getattr(policy, field) is not None else base[field])
        for field in RATE_FIELDS
    }


def allows(mode: str, action: str) -> bool:
    """Non-legacy modes never expose the old full-reset action."""
    if mode == "legacy":
        return action in {"legacy", "traffic", "time", "users", "reset"}
    if mode == "choose":
        return action in {"both", "reset_time", "traffic", "time"}
    return mode == action
