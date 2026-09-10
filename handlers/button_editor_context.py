from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup

from authorization import is_staff
from handlers.premium_ui_clean_buttons import _canonical_label, _load_clean_buttons
from style_engine import style_engine


button_editor_context_router = Router(name="button_editor_context")


# Only customer-facing trial controls belong in the "تست رایگان" editor.
# Admin trial settings use similar callback names, which made the old editor
# mix unrelated controls such as cooldown/traffic/service settings together.
_TRIAL_CONTEXT: dict[str, tuple[int, str]] = {
    "svcmarket:trial": (0, "منوی اصلی → تست رایگان"),
    "trialv2:choose:config": (1, "صفحه تست رایگان → کانفیگ تست"),
    "trialv2:choose:panel": (2, "صفحه تست رایگان → پنل نمایندگی تست"),
    "trialv2:root": (3, "بازگشت → صفحه تست رایگان"),
    "svcmarket:trial:config": (4, "مسیر قدیمی → کانفیگ تست"),
    "svcmarket:trial:panel": (5, "مسیر قدیمی → پنل نمایندگی تست"),
    "ops:configtrial:request": (6, "دسترسی کاربر → کانفیگ تست"),
    "ops:trial:request": (7, "دسترسی کاربر → تست رایگان"),
    "ops:paneltrial:request": (8, "دسترسی کاربر → پنل نمایندگی تست"),
}

_TRIAL_ADMIN_PREFIXES = (
    "ops:trial:",
    "ux:trialadmin:",
    "ux:paneltrial:",
)


def trial_button_context(item) -> tuple[int, str, str] | None:
    callback = str(item.callback_data or "")
    if callback in _TRIAL_CONTEXT:
        rank, context = _TRIAL_CONTEXT[callback]
        current = item.display_text or _canonical_label(item.default_text)
        return rank, context, current
    if callback.startswith(_TRIAL_ADMIN_PREFIXES):
        return None
    return None


async def _btn(text: str, callback_data: str, *, fallback: str | None = None):
    return await style_engine.styled_button(
        text,
        callback_data=callback_data,
        fallback=fallback,
    )


@button_editor_context_router.callback_query(F.data.startswith("uiv2:bc:trial:"))
async def contextual_trial_buttons(callback: CallbackQuery, state: FSMContext):
    if not is_staff(callback.from_user.id):
        await callback.answer("غیرمجاز", show_alert=True)
        return

    await state.clear()
    visible = []
    for item in await _load_clean_buttons():
        parsed = trial_button_context(item)
        if parsed is not None:
            visible.append((parsed[0], parsed[1], parsed[2], item))
    visible.sort(key=lambda row: (row[0], row[3].id))

    rows = []
    for _rank, context, current, item in visible:
        suffix = f" · ✨ {item.emoji_key}" if item.emoji_key else ""
        label = f"{context} | {current}{suffix}"
        rows.append([
            await _btn(label[:60], f"pui:b:{item.id}", fallback="🔘")
        ])

    if not rows:
        rows.append([await _btn("بازگشت", "cc:buttons", fallback="⬅️")])
        text = (
            "🧪 <b>دکمه‌های تست رایگان</b>\n\n"
            "هنوز دکمه‌های کاربری تست در کاتالوگ ثبت نشده‌اند. "
            "یک‌بار /start و سپس صفحه تست رایگان را باز کنید."
        )
    else:
        rows.append([await _btn("بازگشت به بخش‌ها", "cc:buttons", fallback="⬅️")])
        text = (
            "🧪 <b>دکمه‌های تست رایگان — سمت کاربر</b>\n\n"
            "هر دکمه با محل دقیقش نمایش داده می‌شود تا گزینه‌های هم‌نام اشتباه نشوند.\n"
            "تنظیم حجم، مدت، فاصله دریافت و سرویس‌های قابل تست اینجا نمایش داده نمی‌شوند؛ "
            "آن‌ها تنظیمات مدیریتی هستند، نه متن رابط کاربر."
        )

    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()
