from __future__ import annotations

from collections import Counter
from html import escape

from aiogram import F, Router
from aiogram.filters import BaseFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup

import config
from database import db
from product_catalog import ProductCategory, product_catalog
from renewal_policy import allows, policy_for_admin, rates_for_admin
from rebecca_api import rebecca_api
from style_engine import style_engine
from utils.notify import format_traffic_size, seconds_to_days


panel_experience_router = Router(name="canonical_panel_experience")


class RebeccaOnly(BaseFilter):
    async def __call__(self, _event) -> bool:
        return config.PANEL_PROVIDER == "rebecca"


async def _button(text: str, callback_data: str, *, icon_key: str | None = None, fallback: str | None = None):
    return await style_engine.styled_button(
        text,
        callback_data=callback_data,
        icon_key=icon_key,
        fallback=fallback,
    )


async def _category_for_admin(admin) -> ProductCategory | None:
    origin_plan_id = int(getattr(admin, "origin_plan_id", 0) or 0)
    if not origin_plan_id:
        return None
    return await product_catalog.category_for_plan(origin_plan_id)


async def _admin_panel_identity(admin) -> tuple[str, str | None]:
    category = await _category_for_admin(admin)
    if category:
        return category.name, await product_catalog.resolve_panel_icon_key(category)
    # Legacy/manual Rebecca admins may predate plan mappings. Keep them usable,
    # but never substitute a Rebecca inbound/service name here.
    return str(getattr(admin, "admin_name", None) or "پنل نمایندگی"), None


@panel_experience_router.callback_query(F.data == "admin_renew", RebeccaOnly())
async def canonical_renew_entry(callback: CallbackQuery, state: FSMContext):
    admins = await db.get_admins_for_user(callback.from_user.id)
    active_admins = [item for item in admins if bool(getattr(item, "is_active", False))]
    if not active_admins:
        await callback.answer("پنل فعالی ندارید.", show_alert=True)
        return

    identities: list[tuple[object, str, str | None]] = []
    for admin in active_admins:
        name, icon_key = await _admin_panel_identity(admin)
        identities.append((admin, name, icon_key))
    counts = Counter(name for _, name, _ in identities)

    rows = []
    for admin, name, icon_key in identities:
        label = name
        # If the customer owns two instances of one panel family, append only
        # their own panel label so the choices remain distinguishable.
        if counts[name] > 1:
            instance = str(getattr(admin, "admin_name", None) or "").strip()
            if instance and instance != name:
                label = f"{name} · {instance}"
        rows.append([
            await _button(
                label,
                f"admin_renew_panel_{int(admin.id)}",
                icon_key=icon_key,
                fallback="📦" if not icon_key else None,
            )
        ])
    rows.append([await _button("بازگشت", "back_to_admin_main", icon_key="back", fallback="⬅️")])

    text = str(config.MESSAGES.get(
        "renew_panel_select",
        "🔄 <b>تمدید و افزایش</b>\n\nپنل موردنظر را انتخاب کنید:",
    ))
    await state.clear()
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


@panel_experience_router.callback_query(F.data.startswith("admin_renew_panel_"), RebeccaOnly())
async def canonical_renew_panel(callback: CallbackQuery, state: FSMContext):
    try:
        admin_id = int((callback.data or "").rsplit("_", 1)[-1])
    except ValueError:
        await callback.answer("درخواست نامعتبر است.", show_alert=True)
        return
    admin = await db.get_admin_by_id(admin_id)
    if not admin or int(admin.user_id) != int(callback.from_user.id):
        await callback.answer("پنل یافت نشد.", show_alert=True)
        return

    await state.update_data(current_admin_id=admin_id)
    panel_name, icon_key = await _admin_panel_identity(admin)
    icon = await style_engine.render_emoji(icon_key, fallback="📦") if icon_key else "📦"
    heading = f"{icon} <b>{escape(panel_name)}</b>"

    rates = await rates_for_admin(admin)
    category_id, policy = await policy_for_admin(admin)
    if policy.mode != "legacy":
        unlimited_traffic = int(getattr(admin, "max_total_traffic", 0) or 0) == 0
        rows = []
        if allows(policy.mode, "both") and not unlimited_traffic and category_id:
            rows.append([await _button(
                "حجم و زمان با هم · انتخاب پلن",
                f"admin_combo_renew_{admin_id}", icon_key="renew", fallback="♻️",
            )])
        if allows(policy.mode, "traffic") and not unlimited_traffic and rates["per_gb_toman"] > 0:
            rows.append([await _button(
                f"فقط حجم · هر ۱ گیگ {rates['per_gb_toman']:,} ت",
                f"admin_renew_traffic_{admin_id}", icon_key="plan", fallback="📦",
            )])
        # An unlimited-data account is time-billed; no finite quota is added.
        if (allows(policy.mode, "time") or unlimited_traffic) and rates["per_30days_toman"] > 0:
            rows.append([await _button(
                f"فقط زمان · هر ۳۰ روز {rates['per_30days_toman']:,} ت",
                f"admin_renew_time_{admin_id}", icon_key="calendar", fallback="📅",
            )])
        rows.append([await _button("بازگشت", "admin_renew", icon_key="back", fallback="⬅️")])
        intro = (
            "برای تمدید این پنل نوع خرید را انتخاب کنید.\n"
            "حجم و زمان با هم از مشخصات پلن خریداری‌شده اضافه می‌شوند.\n"
            "برای پنل‌های نامحدود فقط تمدید زمان معنی دارد."
        ) if len(rows) > 1 else "برای این پنل تعرفه تمدید فعالی تعریف نشده است."
        await callback.message.edit_text(
            f"{heading}\n\n{intro}", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )
        await callback.answer()
        return

    try:
        override = getattr(admin, "allow_incremental_renewal", None)
        if override is not None:
            allow_incremental = bool(override)
        else:
            plan = await db.get_plan_by_id(int(getattr(admin, "origin_plan_id", 0) or 0))
            allow_incremental = bool(getattr(plan, "allow_incremental_renewal", True)) if plan else True
    except Exception:
        allow_incremental = True

    if allow_incremental:
        rows = [
            [await _button(
                f"حجم · 1GB = {rates['per_gb_toman']:,} ت",
                f"admin_renew_traffic_{admin_id}",
                fallback="➕",
            )],
            [await _button(
                f"زمان · 30 روز = {rates['per_30days_toman']:,} ت",
                f"admin_renew_time_{admin_id}",
                fallback="➕",
            )],
            [await _button(
                f"کاربر · 1 نفر = {rates['per_user_toman']:,} ت",
                f"admin_renew_users_{admin_id}",
                fallback="➕",
            )],
            [await _button("بازگشت", "admin_renew", icon_key="back", fallback="⬅️")],
        ]
        intro = str(config.MESSAGES.get("renew_intro", "مقدار موردنظر برای افزایش را انتخاب کنید."))
    else:
        rows = [
            [await _button(
                "تمدید کامل و انتخاب پلن",
                f"admin_full_renew_{admin_id}",
                icon_key="renew",
                fallback="🔁",
            )],
            [await _button("بازگشت", "admin_renew", icon_key="back", fallback="⬅️")],
        ]
        intro = "برای این پنل تمدید کامل پلن فعال است."

    await callback.message.edit_text(
        f"{heading}\n\n{intro}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@panel_experience_router.callback_query(
    F.data.startswith("admin_full_renew_") & (~F.data.startswith("admin_full_renew_plan_")),
    RebeccaOnly(),
)
async def canonical_full_renew(callback: CallbackQuery, state: FSMContext):
    try:
        admin_id = int((callback.data or "").rsplit("_", 1)[-1])
    except ValueError:
        await callback.answer("درخواست نامعتبر است.", show_alert=True)
        return
    admin = await db.get_admin_by_id(admin_id)
    if not admin or int(admin.user_id) != int(callback.from_user.id):
        await callback.answer("پنل یافت نشد.", show_alert=True)
        return

    _, policy = await policy_for_admin(admin)
    if policy.mode != "legacy":
        await callback.answer("این پنل از روش‌های تمدید جدید استفاده می‌کند.", show_alert=True)
        return

    try:
        override = getattr(admin, "allow_incremental_renewal", None)
        if override is not None and bool(override):
            await callback.answer("برای این پنل افزایش تدریجی فعال است.", show_alert=True)
            return
        origin_plan = await db.get_plan_by_id(int(getattr(admin, "origin_plan_id", 0) or 0))
        if origin_plan and bool(getattr(origin_plan, "allow_incremental_renewal", True)) and override is None:
            await callback.answer("برای این پنل افزایش تدریجی فعال است.", show_alert=True)
            return
    except Exception:
        origin_plan = None

    category = await _category_for_admin(admin)
    if category:
        plans = await product_catalog.plans_for_category(category.id, only_active=True)
        panel_name = category.name
        icon_key = await product_catalog.resolve_panel_icon_key(category)
    else:
        # Compatibility for old/manual accounts without origin_plan_id.
        plans = await db.get_plans(only_active=True)
        panel_name, icon_key = await _admin_panel_identity(admin)

    if not plans:
        await callback.answer("هیچ پلن فعالی برای تمدید این پنل موجود نیست.", show_alert=True)
        return

    icon = await style_engine.render_emoji(icon_key, fallback="📦") if icon_key else "📦"
    lines = [f"{icon} <b>{escape(panel_name)}</b>", "پلن جدید را برای تمدید انتخاب کنید:"]
    rows = []
    for plan in plans:
        traffic = "نامحدود" if plan.traffic_limit_bytes is None else await format_traffic_size(plan.traffic_limit_bytes)
        duration = "نامحدود" if plan.time_limit_seconds is None else f"{seconds_to_days(plan.time_limit_seconds)} روز"
        users = "نامحدود" if plan.max_users is None else f"{plan.max_users} کاربر"
        lines.append(
            f"<b>{escape(str(plan.name))}</b> · {int(plan.price or 0):,} تومان\n"
            f"{escape(str(traffic))} · {escape(str(duration))} · {escape(str(users))}"
        )
        rows.append([
            await _button(
                f"{plan.name} · {int(plan.price or 0):,} ت",
                f"admin_full_renew_plan_{admin_id}_{int(plan.id)}",
                icon_key="plan",
                fallback="📦",
            )
        ])
    rows.append([await _button("بازگشت", f"admin_renew_panel_{admin_id}", icon_key="back", fallback="⬅️")])
    await state.clear()
    await callback.message.edit_text(
        "\n\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@panel_experience_router.callback_query(
    F.data.startswith("admin_combo_renew_") & (~F.data.startswith("admin_combo_renew_plan_")),
    RebeccaOnly(),
)
async def canonical_combined_renew(callback: CallbackQuery, state: FSMContext):
    """Choose a fixed plan whose time and quota are BOTH added, not reset."""
    try:
        admin_id = int((callback.data or "").rsplit("_", 1)[-1])
    except ValueError:
        await callback.answer("درخواست نامعتبر است.", show_alert=True)
        return
    admin = await db.get_admin_by_id(admin_id)
    if not admin or int(admin.user_id) != int(callback.from_user.id):
        await callback.answer("پنل متعلق به شما نیست.", show_alert=True)
        return
    category_id, policy = await policy_for_admin(admin)
    if not category_id or not allows(policy.mode, "both"):
        await callback.answer("تمدید هم‌زمان برای این پنل فعال نیست.", show_alert=True)
        return
    if int(getattr(admin, "max_total_traffic", 0) or 0) <= 0:
        await callback.answer("حجم این پنل نامحدود است؛ فقط تمدید زمان را انتخاب کنید.", show_alert=True)
        return
    category = await product_catalog.get_category(category_id)
    plans = await product_catalog.plans_for_category(category_id, only_active=True)
    valid = [
        plan for plan in plans
        if int(getattr(plan, "traffic_limit_bytes", 0) or 0) > 0
        and int(getattr(plan, "time_limit_seconds", 0) or 0) > 0
        and int(getattr(plan, "price", 0) or 0) > 0
    ]
    if not valid:
        await callback.answer("پلن دارای حجم، مدت و قیمت معتبر وجود ندارد.", show_alert=True)
        return
    rows = []
    for plan in valid:
        gb = int(plan.traffic_limit_bytes) / 1024**3
        days = int(plan.time_limit_seconds) // 86400
        rows.append([await _button(
            f"{plan.name} · +{gb:g}GB / +{days}روز · {int(plan.price):,} ت",
            f"admin_combo_renew_plan_{admin_id}_{int(plan.id)}",
            icon_key="renew", fallback="♻️",
        )])
    rows.append([await _button(
        "بازگشت", f"admin_renew_panel_{admin_id}", icon_key="back", fallback="⬅️",
    )])
    await state.clear()
    await callback.message.edit_text(
        f"♻️ <b>افزایش هم‌زمان {escape(category.name)}</b>\n\n"
        "با خرید هر پلن، حجم و مدت همان پلن به سقف فعلی پنل اضافه می‌شود. "
        "زمان از تاریخ انقضای فعلی (یا امروز، اگر منقضی شده باشد) محاسبه می‌شود. "
        "مصرف قبلی پاک نمی‌شود.\n\nپلن موردنظر را انتخاب کنید:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@panel_experience_router.callback_query(F.data.startswith("admin_combo_renew_plan_"), RebeccaOnly())
async def canonical_combined_renew_plan(callback: CallbackQuery, state: FSMContext):
    """Validate owner, catalog, live limits and quote BEFORE issuing an order."""
    try:
        admin_id, plan_id = (int(x) for x in (callback.data or "").rsplit("_", 2)[-2:])
    except ValueError:
        await callback.answer("درخواست نامعتبر است.", show_alert=True)
        return
    admin = await db.get_admin_by_id(admin_id)
    if not admin or int(admin.user_id) != int(callback.from_user.id):
        await callback.answer("پنل متعلق به شما نیست.", show_alert=True)
        return
    category_id, policy = await policy_for_admin(admin)
    if not category_id or not allows(policy.mode, "both"):
        await callback.answer("این روش تمدید غیرفعال شده است.", show_alert=True)
        return
    plans = await product_catalog.plans_for_category(category_id, only_active=True)
    plan = next((x for x in plans if int(x.id) == plan_id), None)
    if not plan or min(
        int(getattr(plan, "traffic_limit_bytes", 0) or 0),
        int(getattr(plan, "time_limit_seconds", 0) or 0),
        int(getattr(plan, "price", 0) or 0),
    ) <= 0:
        await callback.answer("پلن یا تعرفه انتخاب‌شده نامعتبر است.", show_alert=True)
        return
    # Pay only for an actually extendable account; never make an order that
    # will necessarily fail at the Rebecca quota update.
    try:
        remote = await rebecca_api.find_admin(str(admin.marzban_username or ""))
        if (not isinstance(remote, dict)
                or remote.get("username") != admin.marzban_username
                or type(remote.get("data_limit")) is not int
                or int(remote["data_limit"]) <= 0
                or type(remote.get("expire")) is not int
                or int(remote["expire"]) <= 0):
            await callback.answer("محدودیت پنل نامحدود/نامعتبر است؛ تمدید زمانی را انتخاب کنید.", show_alert=True)
            return
    except Exception:
        await callback.answer("بررسی محدودیت پنل ممکن نیست؛ سفارش ثبت نشد.", show_alert=True)
        return

    order_id = await db.add_order(
        callback.from_user.id,
        plan_id=int(plan.id),
        price_snapshot=int(plan.price),
        plan_name_snapshot=f"افزایش حجم و زمان · {plan.name}",
    )
    if not order_id:
        await callback.answer("ثبت سفارش ناموفق بود.", show_alert=True)
        return
    if not await db.update_order(
        order_id, order_type="renew", target_admin_id=admin_id,
        apply_as_reset=0, delta_traffic_bytes=int(plan.traffic_limit_bytes),
        delta_time_seconds=int(plan.time_limit_seconds), delta_users=0,
    ):
        await callback.answer("تکمیل سفارش ناموفق بود؛ لطفاً با پشتیبانی تماس بگیرید.", show_alert=True)
        return

    # Standard manual checkout and the existing idempotent Rebecca journal
    # remain the only paths that can authorize/execute a paid renewal.
    from handlers.admin_handlers import _mirror_admin_order_created
    from handlers.operations import render_checkout_payment
    await _mirror_admin_order_created(callback.bot, order_id)
    await state.clear()
    await render_checkout_payment(
        callback.message, await db.get_order_by_id(order_id), source="a",
    )
    await callback.answer()
