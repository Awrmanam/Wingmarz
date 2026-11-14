"""Owner-facing renewal mode and service-specific price editor."""
from __future__ import annotations

from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from authorization import is_staff
from database import db
from product_catalog import product_catalog
from renewal_policy import (
    MODES, RATE_FIELDS, RenewalPolicyError, get_policy, save_mode, save_rate,
)
from style_engine import style_engine


renewal_settings_router = Router(name="renewal_settings")


class RenewalSettingsStates(StatesGroup):
    price = State()


async def _button(text: str, cb: str, *, icon: str | None = None, fallback: str | None = None):
    return await style_engine.styled_button(text, callback_data=cb, icon_key=icon, fallback=fallback)


async def _show(message: Message, category_id: int) -> None:
    category = await product_catalog.get_category(category_id)
    if not category:
        await message.edit_text("این دسته محصول وجود ندارد.")
        return
    policy = await get_policy(category_id)
    base = await db.get_billing_rates()
    labels = {
        "per_gb_toman": "هر ۱ گیگابایت",
        "per_30days_toman": "هر ۳۰ روز",
        "per_user_toman": "هر کاربر اضافه",
    }
    lines = [
        f"⚙️ <b>تمدید {escape(category.name)}</b>",
        f"روش فعلی: <b>{escape(MODES[policy.mode])}</b>",
        "",
        "<b>تعرفه‌های افزایش (تومان):</b>",
    ]
    rows = []
    for mode, label in MODES.items():
        rows.append([await _button(
            ("✓ " if mode == policy.mode else "") + label,
            f"renewcfg:mode:{category_id}:{mode}",
            icon="renew", fallback="♻️",
        )])
    for field in RATE_FIELDS:
        value = policy.rate_override(field)
        effective = int(value if value is not None else base[field])
        mark = "اختصاصی" if value is not None else "سراسری"
        lines.append(f"• {labels[field]}: <b>{effective:,}</b> ({mark})")
        rows.append([await _button(
            f"💰 قیمت {labels[field]}", f"renewcfg:rate:{category_id}:{field}",
            icon="price", fallback="💰",
        )])
    lines.extend((
        "",
        "<i>برای قیمت اختصاصی عدد بفرستید؛ برای بازگشت به نرخ سراسری «-» وارد کنید.</i>",
        "<i>نرخ صفر یعنی آن افزایش برای فروش فعال نیست.</i>",
        "<i>«حجم و زمان» از مشخصات و قیمت خود پلن استفاده می‌کند.</i>",
        "<i>تنظیم جدید فقط روی سفارش‌های تازه اثر دارد.</i>",
    ))
    rows.append([await _button("بازگشت به محصول", f"pc:cat:{category_id}", icon="back", fallback="⬅️")])
    await message.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@renewal_settings_router.callback_query(F.data.startswith("renewcfg:root:"))
async def renewal_config_root(callback: CallbackQuery, state: FSMContext):
    if not is_staff(callback.from_user.id):
        await callback.answer("غیرمجاز", show_alert=True)
        return
    try:
        category_id = int(callback.data.rsplit(":", 1)[-1])
        await state.clear()
        await _show(callback.message, category_id)
    except (ValueError, RenewalPolicyError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer()


@renewal_settings_router.callback_query(F.data.startswith("renewcfg:mode:"))
async def renewal_mode_set(callback: CallbackQuery, state: FSMContext):
    if not is_staff(callback.from_user.id):
        await callback.answer("غیرمجاز", show_alert=True)
        return
    try:
        _, _, category_raw, mode = (callback.data or "").split(":")
        category_id = int(category_raw)
        if not await product_catalog.get_category(category_id):
            raise RenewalPolicyError("دسته محصول پیدا نشد.")
        await save_mode(category_id, mode)
        await state.clear()
        await _show(callback.message, category_id)
    except (ValueError, RenewalPolicyError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer("روش تمدید ذخیره شد.")


@renewal_settings_router.callback_query(F.data.startswith("renewcfg:rate:"))
async def renewal_rate_begin(callback: CallbackQuery, state: FSMContext):
    if not is_staff(callback.from_user.id):
        await callback.answer("غیرمجاز", show_alert=True)
        return
    try:
        _, _, category_raw, field = (callback.data or "").split(":")
        category_id = int(category_raw)
        if field not in RATE_FIELDS or not await product_catalog.get_category(category_id):
            raise RenewalPolicyError("نوع تعرفه یا دسته محصول نامعتبر است.")
    except (ValueError, RenewalPolicyError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await state.clear()
    await state.update_data(renew_category_id=category_id, renew_rate_field=field)
    await state.set_state(RenewalSettingsStates.price)
    await callback.message.edit_text(
        "💰 نرخ جدید را به تومان و فقط عدد بفرستید.\n"
        "عدد ۰ = غیرفعال‌کردن فروش این افزایش\n"
        "علامت - = استفاده از تعرفه سراسری",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            await _button("انصراف", f"renewcfg:root:{category_id}", icon="back", fallback="⬅️")
        ]]),
    )
    await callback.answer()


@renewal_settings_router.message(RenewalSettingsStates.price, F.text)
async def renewal_rate_save(message: Message, state: FSMContext):
    if not is_staff(message.from_user.id):
        await state.clear()
        return
    data = await state.get_data()
    category_id = int(data.get("renew_category_id") or 0)
    field = str(data.get("renew_rate_field") or "")
    raw = (message.text or "").strip().replace(",", "").replace("٬", "")
    try:
        if field not in RATE_FIELDS or not await product_catalog.get_category(category_id):
            raise RenewalPolicyError("درخواست تغییر تعرفه نامعتبر است.")
        price = None if raw == "-" else int(raw)
        await save_rate(category_id, field, price)
    except (ValueError, RenewalPolicyError) as exc:
        await message.answer(f"❌ {escape(str(exc))}\nعدد درست را بفرستید یا «-» ارسال کنید.")
        return
    await state.clear()
    await message.answer(
        "✅ تعرفه محصول ذخیره شد.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            await _button("برگشت به تعرفه‌های پنل", f"renewcfg:root:{category_id}", icon="back", fallback="⬅️")
        ]]),
    )
