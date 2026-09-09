from __future__ import annotations

from html import escape
import math
from typing import Any

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from authorization import is_staff
from database import db
from product_catalog import product_catalog
from service_marketplace_service import service_marketplace_service
from style_engine import style_engine


product_center_router = Router(name="product_center")


class ProductEditStates(StatesGroup):
    value = State()
    create_name = State()
    create_description = State()
    create_price = State()
    create_traffic = State()
    create_days = State()
    create_users = State()


def _is_staff(user_id: int) -> bool:
    return is_staff(user_id)


async def _button(text: str, callback_data: str, *, fallback: str | None = None, icon_key: str | None = None):
    return await style_engine.styled_button(
        text,
        callback_data=callback_data,
        fallback=fallback,
        icon_key=icon_key,
    )


def _traffic_text(value: int | None) -> str:
    if not value:
        return "نامحدود"
    gb = float(value) / (1024 ** 3)
    return f"{gb:g} GB"


def _duration_text(value: int | None) -> str:
    if not value:
        return "نامحدود"
    days = max(1, math.ceil(int(value) / 86400))
    return service_marketplace_service.duration_label(int(value)) if days >= 1 else f"{value} ثانیه"


def _users_text(value: int | None) -> str:
    return "نامحدود" if value is None else str(int(value))


async def _deny(callback: CallbackQuery) -> bool:
    if _is_staff(callback.from_user.id):
        return False
    await callback.answer("غیرمجاز", show_alert=True)
    return True


async def _root(message: Message) -> None:
    categories = await product_catalog.categories()
    grouping = await service_marketplace_service.duration_groups_enabled()
    total_plans = len(await db.get_plans())
    active_plans = len(await db.get_plans(only_active=True))
    lines = [
        "📦 <b>مرکز محصولات و پلن‌ها</b>",
        "",
        f"دسته‌های کاربری: <b>{len(categories)}</b>",
        f"پلن‌ها: <b>{active_plans}</b> فعال از <b>{total_plans}</b>",
        f"دسته‌بندی زمانی: <b>{'فعال' if grouping else 'غیرفعال'}</b>",
        "",
        "نام داخلی Rebecca/Inbound فقط برای مسیریابی است و به کاربر نمایش داده نمی‌شود.",
        "از این بخش نام نمایشی، توضیحات، قیمت، حجم، زمان و تعداد کاربر را مدیریت کنید.",
    ]
    rows = []
    for category in categories:
        plans = await product_catalog.plans_for_category(category.id, only_active=False)
        status = "✅" if category.is_active else "⛔"
        rows.append([
            await _button(
                f"{status} {category.name} · {len(plans)} پلن",
                f"pc:cat:{category.id}",
                fallback="📁",
            )
        ])
    rows.extend([
        [await _button(
            f"🗂 دسته‌بندی زمانی: {'روشن' if grouping else 'خاموش'}",
            "pc:duration:toggle",
            fallback="🗂",
        )],
        [await _button("🔌 مدیریت سرویس‌های Rebecca", "rebecca_services", fallback="🔌")],
        [await _button("بازگشت", "sudo_menu_sales", icon_key="back", fallback="⬅️")],
    ])
    await message.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@product_center_router.callback_query(F.data.in_({"sales_manage", "pc:root"}))
async def product_center_root(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    await state.clear()
    await _root(callback.message)
    await callback.answer()


@product_center_router.callback_query(F.data == "pc:duration:toggle")
async def product_center_duration_toggle(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    current = await service_marketplace_service.duration_groups_enabled()
    await service_marketplace_service.set_duration_groups_enabled(not current)
    await state.clear()
    await _root(callback.message)
    await callback.answer("بروزرسانی شد")


async def _category_detail(message: Message, category_id: int) -> None:
    category = await product_catalog.get_category(category_id)
    if not category:
        await message.edit_text("این دسته پیدا نشد.")
        return
    plans = await product_catalog.plans_for_category(category.id, only_active=False)
    provider = escape(str(category.provider_name or "ثبت نشده"))
    desc = escape(category.description) if category.description else "—"
    text = (
        f"📁 <b>{escape(category.name)}</b>\n\n"
        f"وضعیت نمایش: <b>{'✅ فعال' if category.is_active else '⛔ غیرفعال'}</b>\n"
        f"پلن‌های متصل: <b>{len(plans)}</b>\n"
        f"توضیحات: {desc}\n\n"
        f"🔒 Inbound داخلی: <code>{provider}</code>\n"
        "<i>این نام داخلی به کاربر نمایش داده نمی‌شود.</i>"
    )
    rows = [
        [
            await _button("✏️ نام نمایشی", f"pc:editcat:name:{category.id}"),
            await _button("📝 توضیحات", f"pc:editcat:description:{category.id}"),
        ],
        [await _button(
            "⛔ غیرفعال‌کردن" if category.is_active else "✅ فعال‌کردن",
            f"pc:cattoggle:{category.id}",
        )],
        [await _button("📦 پلن‌های این دسته", f"pc:plans:{category.id}")],
        [await _button("➕ ساخت پلن جدید", f"pc:new:{category.id}", fallback="➕")],
        [await _button("بازگشت", "pc:root", icon_key="back", fallback="⬅️")],
    ]
    await message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@product_center_router.callback_query(F.data.startswith("pc:cat:"))
async def product_center_category(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    await state.clear()
    try:
        category_id = int(callback.data.rsplit(":", 1)[-1])
    except ValueError:
        await callback.answer("نامعتبر", show_alert=True)
        return
    await _category_detail(callback.message, category_id)
    await callback.answer()


@product_center_router.callback_query(F.data.startswith("pc:cattoggle:"))
async def product_center_category_toggle(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    category_id = int(callback.data.rsplit(":", 1)[-1])
    category = await product_catalog.get_category(category_id)
    if not category:
        await callback.answer("دسته پیدا نشد", show_alert=True)
        return
    await product_catalog.update_category(category_id, is_active=not category.is_active)
    await state.clear()
    await _category_detail(callback.message, category_id)
    await callback.answer("بروزرسانی شد")


@product_center_router.callback_query(F.data.startswith("pc:editcat:"))
async def product_center_edit_category(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    parts = (callback.data or "").split(":")
    if len(parts) != 4 or parts[2] not in {"name", "description"}:
        await callback.answer("نامعتبر", show_alert=True)
        return
    category_id = int(parts[3])
    await state.clear()
    await state.update_data(pc_kind="category", pc_field=parts[2], pc_category_id=category_id)
    await state.set_state(ProductEditStates.value)
    prompt = "نام نمایشی جدید را بفرستید:" if parts[2] == "name" else "توضیحات جدید را بفرستید. برای حذف توضیحات فقط - بفرستید:"
    await callback.message.edit_text(prompt, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
        await _button("انصراف", f"pc:cat:{category_id}", fallback="⬅️")
    ]]))
    await callback.answer()


async def _plans_list(message: Message, category_id: int) -> None:
    category = await product_catalog.get_category(category_id)
    if not category:
        await message.edit_text("دسته پیدا نشد.")
        return
    plans = await product_catalog.plans_for_category(category_id, only_active=False)
    rows = []
    for plan in plans:
        status = "✅" if bool(plan.is_active) else "⛔"
        rows.append([
            await _button(
                f"{status} {plan.name} · {int(plan.price or 0):,} ت",
                f"pc:plan:{category_id}:{int(plan.id)}",
                fallback="📦",
            )
        ])
    if not rows:
        rows.append([await _button("➕ ساخت اولین پلن", f"pc:new:{category_id}", fallback="➕")])
    else:
        rows.append([await _button("➕ ساخت پلن جدید", f"pc:new:{category_id}", fallback="➕")])
    rows.append([await _button("بازگشت", f"pc:cat:{category_id}", icon_key="back", fallback="⬅️")])
    await message.edit_text(
        f"📦 <b>پلن‌های {escape(category.name)}</b>\n\nپلن موردنظر را برای ویرایش انتخاب کنید:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@product_center_router.callback_query(F.data.startswith("pc:plans:"))
async def product_center_plans(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    await state.clear()
    category_id = int(callback.data.rsplit(":", 1)[-1])
    await _plans_list(callback.message, category_id)
    await callback.answer()


async def _plan_detail(message: Message, category_id: int, plan_id: int) -> None:
    plan = await db.get_plan_by_id(plan_id)
    category = await product_catalog.get_category(category_id)
    if not plan or not category:
        await message.edit_text("پلن یا دسته پیدا نشد.")
        return
    description = await product_catalog.plan_description(plan_id)
    text = (
        f"📦 <b>{escape(plan.name)}</b>\n"
        f"📁 دسته: <b>{escape(category.name)}</b>\n\n"
        f"📝 توضیحات: {escape(description) if description else '—'}\n"
        f"💵 قیمت: <b>{int(plan.price or 0):,} تومان</b>\n"
        f"📊 حجم: <b>{_traffic_text(plan.traffic_limit_bytes)}</b>\n"
        f"🗓 زمان: <b>{_duration_text(plan.time_limit_seconds)}</b>\n"
        f"👥 کاربران: <b>{_users_text(plan.max_users)}</b>\n"
        f"وضعیت: <b>{'✅ فعال' if plan.is_active else '⛔ غیرفعال'}</b>"
    )
    rows = [
        [
            await _button("✏️ نام", f"pc:editplan:name:{category_id}:{plan_id}"),
            await _button("📝 توضیحات", f"pc:editplan:description:{category_id}:{plan_id}"),
        ],
        [
            await _button("💵 قیمت", f"pc:editplan:price:{category_id}:{plan_id}"),
            await _button("📊 حجم", f"pc:editplan:traffic:{category_id}:{plan_id}"),
        ],
        [
            await _button("🗓 زمان", f"pc:editplan:days:{category_id}:{plan_id}"),
            await _button("👥 کاربران", f"pc:editplan:users:{category_id}:{plan_id}"),
        ],
        [await _button(
            "⛔ غیرفعال‌کردن" if plan.is_active else "✅ فعال‌کردن",
            f"pc:plantoggle:{category_id}:{plan_id}",
        )],
        [await _button("🗑 حذف امن", f"pc:deleteask:{category_id}:{plan_id}", fallback="🗑")],
        [await _button("بازگشت", f"pc:plans:{category_id}", icon_key="back", fallback="⬅️")],
    ]
    await message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@product_center_router.callback_query(F.data.startswith("pc:plan:"))
async def product_center_plan(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    parts = callback.data.split(":")
    await state.clear()
    await _plan_detail(callback.message, int(parts[2]), int(parts[3]))
    await callback.answer()


@product_center_router.callback_query(F.data.startswith("pc:plantoggle:"))
async def product_center_plan_toggle(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    _, _, category_id, plan_id = callback.data.split(":")
    plan = await db.get_plan_by_id(int(plan_id))
    if not plan:
        await callback.answer("پلن پیدا نشد", show_alert=True)
        return
    await product_catalog.update_plan_field(int(plan_id), "is_active", 0 if plan.is_active else 1)
    await state.clear()
    await _plan_detail(callback.message, int(category_id), int(plan_id))
    await callback.answer("بروزرسانی شد")


@product_center_router.callback_query(F.data.startswith("pc:editplan:"))
async def product_center_edit_plan(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    parts = callback.data.split(":")
    if len(parts) != 5 or parts[2] not in {"name", "description", "price", "traffic", "days", "users"}:
        await callback.answer("نامعتبر", show_alert=True)
        return
    field, category_id, plan_id = parts[2], int(parts[3]), int(parts[4])
    prompts = {
        "name": "نام جدید پلن را بفرستید:",
        "description": "توضیحات جدید را بفرستید. برای حذف توضیحات - بفرستید:",
        "price": "قیمت جدید را به تومان و فقط عدد بفرستید:",
        "traffic": "حجم را به GB بفرستید. 0 یعنی نامحدود:",
        "days": "مدت را به روز بفرستید. 0 یعنی نامحدود. مثال: 30",
        "users": "حداکثر تعداد کاربر را بفرستید. 0 یعنی نامحدود:",
    }
    await state.clear()
    await state.update_data(pc_kind="plan", pc_field=field, pc_category_id=category_id, pc_plan_id=plan_id)
    await state.set_state(ProductEditStates.value)
    await callback.message.edit_text(prompts[field], reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
        await _button("انصراف", f"pc:plan:{category_id}:{plan_id}", fallback="⬅️")
    ]]))
    await callback.answer()


@product_center_router.message(ProductEditStates.value, F.text)
async def product_center_edit_value(message: Message, state: FSMContext):
    data = await state.get_data()
    value = (message.text or "").strip()
    kind = data.get("pc_kind")
    field = data.get("pc_field")
    category_id = int(data.get("pc_category_id") or 0)
    try:
        if kind == "category":
            if field == "name":
                await product_catalog.update_category(category_id, name=value)
            else:
                await product_catalog.update_category(category_id, description="" if value == "-" else value)
            await state.clear()
            await message.answer("✅ ذخیره شد.")
            # Message states cannot edit the previous prompt reliably; give a compact return button.
            await message.answer("بازگشت به دسته:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                await _button("بازگشت", f"pc:cat:{category_id}", fallback="⬅️")
            ]]))
            return

        plan_id = int(data.get("pc_plan_id") or 0)
        if field == "name":
            if not value or len(value) > 80:
                raise ValueError("نام پلن باید بین ۱ تا ۸۰ کاراکتر باشد.")
            await product_catalog.update_plan_field(plan_id, "name", value)
        elif field == "description":
            await product_catalog.set_plan_description(plan_id, "" if value == "-" else value)
        elif field == "price":
            number = int(value.replace(",", ""))
            if number < 0:
                raise ValueError("قیمت نمی‌تواند منفی باشد.")
            await product_catalog.update_plan_field(plan_id, "price", number)
        elif field == "traffic":
            number = float(value)
            if number < 0:
                raise ValueError("حجم نامعتبر است.")
            await product_catalog.update_plan_field(plan_id, "traffic_limit_bytes", None if number == 0 else int(number * 1024 ** 3))
        elif field == "days":
            number = int(value)
            if number < 0:
                raise ValueError("زمان نامعتبر است.")
            await product_catalog.update_plan_field(plan_id, "time_limit_seconds", None if number == 0 else number * 86400)
        elif field == "users":
            number = int(value)
            if number < 0:
                raise ValueError("تعداد کاربر نامعتبر است.")
            await product_catalog.update_plan_field(plan_id, "max_users", None if number == 0 else number)
        else:
            raise ValueError("فیلد نامعتبر است.")
    except (ValueError, TypeError) as exc:
        await message.answer(f"❌ {escape(str(exc))}\nدوباره مقدار صحیح را بفرستید.")
        return
    await state.clear()
    await message.answer("✅ تغییرات ذخیره شد.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
        await _button("بازگشت به پلن", f"pc:plan:{category_id}:{plan_id}", fallback="⬅️")
    ]]))


@product_center_router.callback_query(F.data.startswith("pc:deleteask:"))
async def product_center_delete_ask(callback: CallbackQuery):
    if await _deny(callback):
        return
    _, _, category_id, plan_id = callback.data.split(":")
    plan = await db.get_plan_by_id(int(plan_id))
    if not plan:
        await callback.answer("پلن پیدا نشد", show_alert=True)
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [await _button("✅ بله، حذف امن", f"pc:deleteyes:{category_id}:{plan_id}")],
        [await _button("انصراف", f"pc:plan:{category_id}:{plan_id}", fallback="⬅️")],
    ])
    await callback.message.edit_text(
        f"🗑 <b>حذف {escape(plan.name)}</b>\n\n"
        "اگر این پلن سابقه سفارش یا پنل فعال داشته باشد، برای حفظ تاریخچه فقط آرشیو و غیرفعال می‌شود. "
        "اگر هیچ وابستگی نداشته باشد، کامل حذف می‌شود.",
        reply_markup=kb,
    )
    await callback.answer()


@product_center_router.callback_query(F.data.startswith("pc:deleteyes:"))
async def product_center_delete_yes(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    _, _, category_id, plan_id = callback.data.split(":")
    result = await product_catalog.safe_delete_plan(int(plan_id))
    await state.clear()
    await _plans_list(callback.message, int(category_id))
    if result == "deleted":
        await callback.answer("پلن حذف شد")
    elif result == "archived":
        await callback.answer("برای حفظ تاریخچه، پلن آرشیو و غیرفعال شد")
    else:
        await callback.answer("پلن پیدا نشد", show_alert=True)


@product_center_router.callback_query(F.data.startswith("pc:new:"))
async def product_center_new(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    category_id = int(callback.data.rsplit(":", 1)[-1])
    if not await product_catalog.get_category(category_id):
        await callback.answer("دسته پیدا نشد", show_alert=True)
        return
    await state.clear()
    await state.update_data(pc_category_id=category_id)
    await state.set_state(ProductEditStates.create_name)
    await callback.message.edit_text("➕ <b>پلن جدید</b>\n\nنام پلن را بفرستید:")
    await callback.answer()


@product_center_router.message(ProductEditStates.create_name, F.text)
async def product_new_name(message: Message, state: FSMContext):
    value = (message.text or "").strip()
    if not value or len(value) > 80:
        await message.answer("نام پلن باید بین ۱ تا ۸۰ کاراکتر باشد.")
        return
    await state.update_data(pc_new_name=value)
    await state.set_state(ProductEditStates.create_description)
    await message.answer("توضیحات پلن را بفرستید. اگر نمی‌خواهید، - بفرستید:")


@product_center_router.message(ProductEditStates.create_description, F.text)
async def product_new_description(message: Message, state: FSMContext):
    value = (message.text or "").strip()
    if len(value) > 800:
        await message.answer("توضیحات حداکثر ۸۰۰ کاراکتر است.")
        return
    await state.update_data(pc_new_description="" if value == "-" else value)
    await state.set_state(ProductEditStates.create_price)
    await message.answer("قیمت پلن را به تومان بفرستید:")


@product_center_router.message(ProductEditStates.create_price, F.text)
async def product_new_price(message: Message, state: FSMContext):
    try:
        value = int((message.text or "").replace(",", "").strip())
        if value < 0:
            raise ValueError
    except ValueError:
        await message.answer("قیمت باید عدد صحیح و غیرمنفی باشد.")
        return
    await state.update_data(pc_new_price=value)
    await state.set_state(ProductEditStates.create_traffic)
    await message.answer("حجم را به GB بفرستید. 0 یعنی نامحدود:")


@product_center_router.message(ProductEditStates.create_traffic, F.text)
async def product_new_traffic(message: Message, state: FSMContext):
    try:
        value = float((message.text or "").strip())
        if value < 0:
            raise ValueError
    except ValueError:
        await message.answer("حجم باید عدد غیرمنفی باشد.")
        return
    await state.update_data(pc_new_traffic=None if value == 0 else int(value * 1024 ** 3))
    await state.set_state(ProductEditStates.create_days)
    await message.answer("مدت پلن را به روز بفرستید. مثال 30؛ عدد 0 یعنی نامحدود:")


@product_center_router.message(ProductEditStates.create_days, F.text)
async def product_new_days(message: Message, state: FSMContext):
    try:
        value = int((message.text or "").strip())
        if value < 0:
            raise ValueError
    except ValueError:
        await message.answer("تعداد روز باید عدد صحیح و غیرمنفی باشد.")
        return
    await state.update_data(pc_new_duration=None if value == 0 else value * 86400)
    await state.set_state(ProductEditStates.create_users)
    await message.answer("حداکثر تعداد کاربر را بفرستید. 0 یعنی نامحدود:")


@product_center_router.message(ProductEditStates.create_users, F.text)
async def product_new_users(message: Message, state: FSMContext):
    try:
        value = int((message.text or "").strip())
        if value < 0:
            raise ValueError
    except ValueError:
        await message.answer("تعداد کاربر باید عدد صحیح و غیرمنفی باشد.")
        return
    data = await state.get_data()
    category_id = int(data["pc_category_id"])
    try:
        plan_id = await product_catalog.create_plan(
            category_id=category_id,
            name=data["pc_new_name"],
            description=data.get("pc_new_description", ""),
            price=int(data["pc_new_price"]),
            traffic_bytes=data.get("pc_new_traffic"),
            duration_seconds=data.get("pc_new_duration"),
            max_users=None if value == 0 else value,
        )
    except ValueError as exc:
        await state.clear()
        await message.answer(f"❌ {escape(str(exc))}")
        return
    await state.clear()
    await message.answer(
        "✅ پلن ساخته شد.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            await _button("مشاهده پلن", f"pc:plan:{category_id}:{plan_id}", fallback="📦")
        ]]),
    )
