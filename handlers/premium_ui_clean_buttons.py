from __future__ import annotations

import re

import aiosqlite
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

import config
from premium_ui_service import ButtonCatalogItem, premium_ui_service
from style_engine import style_engine


premium_ui_clean_buttons_router = Router(name="premium_ui_clean_buttons")

# Keep the SUDO/public navigation buttons at the top of the editor instead of
# showing dynamic user/order rows first.
_TOP_LEVEL_CALLBACKS = [
    "sudo_menu_panels",
    "sudo_menu_sales",
    "cc:orders:0",
    "sales_manage",
    "cc:users:0",
    "cc:discounts",
    "cc:test",
    "cc:botadmins",
    "cc:stats",
    "sudo_menu_broadcast",
    "cc:tickets:0",
    "style:menu",
    "cc:texts",
    "cc:buttons",
    "sudo_menu_backup",
    "sudo_menu_settings",
    "back_to_main",
]
_TOP_LEVEL_RANK = {callback: index for index, callback in enumerate(_TOP_LEVEL_CALLBACKS)}
_INTERNAL_PREFIXES = ("pui:", "puc:")


def _sudo(user_id: int) -> bool:
    return int(user_id) in config.SUDO_ADMINS


async def _btn(text: str, callback_data: str, *, icon_key: str | None = None, fallback: str | None = None):
    return await style_engine.styled_button(
        text,
        callback_data=callback_data,
        icon_key=icon_key,
        fallback=fallback,
    )


def _is_dynamic_noise(item: ButtonCatalogItem) -> bool:
    callback = item.callback_data
    text = (item.display_text or item.default_text or "").strip()
    if callback.startswith(_INTERNAL_PREFIXES):
        return True

    # Hide entity rows such as "👤 356770827" and page counters from the visual
    # editor. Their labels are data, not UI copy, and must not be rewritten.
    cleaned = re.sub(r"^[^\w@]+", "", text, flags=re.UNICODE).strip()
    if re.fullmatch(r"\d{5,}", cleaned):
        return True
    if re.fullmatch(r"\d+\s*/\s*\d+", cleaned):
        return True

    # Common dynamic record callbacks: keep their action buttons in business
    # flows, but do not offer user/order/admin IDs as editable UI labels.
    if re.search(r"(?:^|:)(?:user|order|ticket|admin):\d+$", callback):
        return True
    return False


def _sort_key(item: ButtonCatalogItem) -> tuple[int, int, int]:
    if item.callback_data in _TOP_LEVEL_RANK:
        return (0, _TOP_LEVEL_RANK[item.callback_data], item.id)
    # Newer discovered static buttons after the curated main navigation.
    return (1, 0, -item.id)


async def _load_clean_buttons() -> list[ButtonCatalogItem]:
    await premium_ui_service.ensure_schema()
    async with aiosqlite.connect(premium_ui_service.db_path) as conn:
        conn.row_factory = aiosqlite.Row
        # Remove editor's own controls accumulated by the global keyboard
        # cataloguer. This is presentation-only data and safe to purge.
        await conn.execute("DELETE FROM styled_button_catalog WHERE callback_data LIKE 'pui:%'")
        await conn.execute("DELETE FROM styled_button_catalog WHERE callback_data LIKE 'puc:%'")
        await conn.commit()
        async with conn.execute(
            """
            SELECT c.id,c.callback_data,c.default_text,c.default_icon_key,c.default_fallback,
                   o.display_text,o.emoji_key
            FROM styled_button_catalog c
            LEFT JOIN styled_button_overrides o ON o.button_id=c.id
            ORDER BY c.id DESC
            """
        ) as cur:
            rows = await cur.fetchall()

    items = [premium_ui_service._row_to_button(row) for row in rows]
    items = [item for item in items if not _is_dynamic_noise(item)]
    return sorted(items, key=_sort_key)


async def _render(message: Message, page: int = 0) -> None:
    items = await _load_clean_buttons()
    page_size = 12
    pages = max(1, (len(items) + page_size - 1) // page_size)
    page = max(0, min(int(page), pages - 1))
    visible = items[page * page_size : (page + 1) * page_size]

    rows = []
    lines = [
        "🔘 <b>متن و ایموجی دکمه‌های ربات</b>",
        "",
        "اول دکمه‌های اصلی ربات نمایش داده می‌شوند. دکمه‌های شامل شناسه کاربر/سفارش و کنترل‌های داخلی مخفی شده‌اند.",
        "متن نمایشی و Premium Emoji را می‌توانید تغییر دهید؛ Callback اصلی دست‌نخورده می‌ماند.",
        "",
    ]
    if not visible:
        lines.append("هنوز دکمه قابل‌ویرایشی ثبت نشده؛ یک‌بار منوی موردنظر را باز کنید و برگردید.")

    for item in visible:
        label = item.display_text or item.default_text
        suffix = f" · ✨ {item.emoji_key}" if item.emoji_key else ""
        rows.append([
            await _btn(f"{label}{suffix}"[:55], f"pui:b:{item.id}", fallback="🔘")
        ])

    if pages > 1:
        nav = []
        if page > 0:
            nav.append(await _btn("قبلی", f"puc:bs:{page-1}", fallback="⬅️"))
        nav.append(await _btn(f"{page+1}/{pages}", "puc:noop", fallback="📄"))
        if page + 1 < pages:
            nav.append(await _btn("بعدی", f"puc:bs:{page+1}", fallback="➡️"))
        rows.append(nav)

    rows.extend([
        [await _btn("Premium Emojiها", "style:emojis", icon_key="style", fallback="✨")],
        [await _btn("خانه", "back_to_main", icon_key="home", fallback="🏠")],
    ])
    await message.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@premium_ui_clean_buttons_router.callback_query(F.data == "cc:buttons")
async def clean_buttons_root(callback: CallbackQuery, state: FSMContext):
    if not _sudo(callback.from_user.id):
        await callback.answer("غیرمجاز", show_alert=True)
        return
    await state.clear()
    await _render(callback.message, 0)
    await callback.answer()


@premium_ui_clean_buttons_router.callback_query(F.data.startswith("puc:bs:"))
async def clean_buttons_page(callback: CallbackQuery, state: FSMContext):
    if not _sudo(callback.from_user.id):
        await callback.answer("غیرمجاز", show_alert=True)
        return
    await state.clear()
    try:
        page = int((callback.data or "").rsplit(":", 1)[-1])
    except ValueError:
        page = 0
    await _render(callback.message, page)
    await callback.answer()


@premium_ui_clean_buttons_router.callback_query(F.data == "puc:noop")
async def clean_buttons_noop(callback: CallbackQuery):
    await callback.answer()
