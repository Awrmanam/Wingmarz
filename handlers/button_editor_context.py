from __future__ import annotations

import math
from html import escape

from aiogram import F, Router
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from authorization import is_staff
from handlers.premium_ui_clean_buttons import _canonical_label, _load_clean_buttons
from style_engine import style_engine
from ui_presentation_registry import (
    CATEGORY_META,
    SCOPE_META,
    ButtonDef,
    ScreenDef,
    resolve_button,
    screen_for,
)


button_editor_context_router = Router(name="button_editor_context")


class UIButtonSearchStates(StatesGroup):
    query = State()


async def _btn(text: str, callback_data: str, *, fallback: str | None = None):
    return await style_engine.styled_button(text, callback_data=callback_data, fallback=fallback)


def _current_label(item) -> str:
    return str(getattr(item, "display_text", None) or _canonical_label(getattr(item, "default_text", "")))


async def _classified_items() -> list[tuple[ButtonDef, ScreenDef, object]]:
    """Return only explicitly registered, genuinely editable logical buttons.

    The runtime catalog is discovery/storage only. Screen taxonomy comes from the
    explicit presentation registry, so provider records, IDs, compatibility
    aliases and internal editor controls can never leak into the normal UI.
    """
    rows: list[tuple[ButtonDef, ScreenDef, object]] = []
    seen: set[str] = set()
    for item in await _load_clean_buttons():
        resolution = resolve_button(item.callback_data, item.default_text)
        logical = resolution.button
        if logical is None or logical.key in seen:
            continue
        screen = screen_for(logical.screen)
        if screen is None:
            continue
        seen.add(logical.key)
        rows.append((logical, screen, item))
    return rows


async def _render_root(message) -> None:
    rows = []
    classified = await _classified_items()
    for scope, (title, emoji) in SCOPE_META.items():
        count = sum(1 for _logical, screen, _item in classified if screen.scope == scope)
        if count:
            rows.append([await _btn(f"{emoji} {title}", f"uiv4:scope:{scope}")])
    rows.extend([
        [await _btn("🔎 جستجوی دکمه", "uiv4:search", fallback="🔎")],
        [await _btn("Premium Emojiها", "style:emojis", fallback="✨")],
        [await _btn("بازگشت به مرکز ظاهر و محتوا", "ops:ui:home", fallback="⬅️")],
    ])
    await message.edit_text(
        "🔘 <b>مدیریت دکمه‌ها</b>\n\n"
        "اینجا فقط دکمه‌هایی نمایش داده می‌شوند که واقعاً بخشی از رابط ثابت ربات هستند.\n"
        "نام پنل، پلن، کاربر، سفارش، تیکت و سایر داده‌های پویا از مدیر همان بخش تغییر می‌کنند.\n\n"
        "برای پیدا کردن یک دکمه: <b>نوع رابط → بخش → صفحه → دکمه</b> را انتخاب کنید.\n"
        "متن و Premium Emoji قابل تغییر است؛ عملکرد اصلی دکمه دست‌نخورده می‌ماند.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@button_editor_context_router.callback_query(F.data == "cc:buttons")
async def global_button_editor(callback: CallbackQuery, state: FSMContext):
    if not is_staff(callback.from_user.id):
        await callback.answer("غیرمجاز", show_alert=True)
        return
    await state.clear()
    await _render_root(callback.message)
    await callback.answer()


def search_registered_buttons(
    items: list[tuple[ButtonDef, ScreenDef, object]], query: str,
) -> list[tuple[ButtonDef, ScreenDef, object]]:
    """Search visible button labels and page titles, not callbacks or IDs."""
    needle = str(query or "").replace("\u200c"," ").casefold().strip()
    if not needle:
        return []
    matches = []
    for logical, screen, item in items:
        haystack = " ".join([
            logical.title, screen.title, _current_label(item),
            str(getattr(item,"default_text","")),
        ]).replace("\u200c"," ").casefold()
        if needle in haystack:
            matches.append((logical,screen,item))
    return sorted(matches, key=lambda row: (row[1].rank,row[0].rank,row[0].title))


@button_editor_context_router.callback_query(F.data == "uiv4:search")
async def button_search_start(callback: CallbackQuery, state: FSMContext):
    if not is_staff(callback.from_user.id):
        await callback.answer("غیرمجاز", show_alert=True)
        return
    await state.clear()
    await state.set_state(UIButtonSearchStates.query)
    await callback.message.answer(
        "🔎 <b>جستجوی دکمه</b>\n\n"
        "نام دکمه یا صفحه آن را بنویسید (۲ تا ۶۰ کاراکتر).",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [await _btn("انصراف و بازگشت", "cc:buttons", fallback="⬅️")],
        ]),
    )
    await callback.answer()


@button_editor_context_router.message(UIButtonSearchStates.query, F.text)
async def button_search_result(message: Message, state: FSMContext):
    if not message.from_user or not is_staff(message.from_user.id):
        return
    query = (message.text or "").strip()
    if not 2 <= len(query) <= 60:
        await message.answer("عبارت جستجو باید ۲ تا ۶۰ کاراکتر باشد.")
        return
    results = search_registered_buttons(await _classified_items(),query)
    await state.clear()
    rows = [
        [await _btn(
            f"{screen.title} | {_current_label(item)}"[:60],
            f"pui:b:{item.id}",
        )]
        for logical,screen,item in results[:12]
    ]
    rows.extend([
        [await _btn("جستجوی دوباره", "uiv4:search", fallback="🔎")],
        [await _btn("بازگشت به بخش‌های دکمه", "cc:buttons", fallback="⬅️")],
    ])
    await message.answer(
        "🔎 <b>نتیجه جستجوی دکمه‌ها</b>\n"
        f"عبارت: <b>{escape(query)}</b>\n"
        f"تعداد نتیجه: <b>{len(results)}</b>\n"
        + ("برای نتایج بیشتر عبارت دقیق‌تری وارد کنید."
           if len(results)>12 else
           "دکمه موردنظر را انتخاب کنید." if results else
           "دکمه‌ای پیدا نشد."),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@button_editor_context_router.callback_query(F.data.startswith("uiv4:scope:"))
async def scope_menu(callback: CallbackQuery, state: FSMContext):
    if not is_staff(callback.from_user.id):
        await callback.answer("غیرمجاز", show_alert=True)
        return
    await state.clear()
    scope = (callback.data or "").rsplit(":", 1)[-1]
    if scope not in SCOPE_META:
        await callback.answer("بخش نامعتبر", show_alert=True)
        return

    classified = await _classified_items()
    categories: dict[str, int] = {}
    for _logical, screen, _item in classified:
        if screen.scope == scope:
            categories[screen.category] = categories.get(screen.category, 0) + 1

    rows = []
    for category in CATEGORY_META:
        count = categories.get(category, 0)
        if not count:
            continue
        title, emoji = CATEGORY_META[category]
        rows.append([await _btn(f"{emoji} {title}", f"uiv4:cat:{scope}:{category}")])
    rows.append([await _btn("بازگشت", "cc:buttons", fallback="⬅️")])

    title, emoji = SCOPE_META[scope]
    await callback.message.edit_text(
        f"{emoji} <b>{title}</b>\n\nبخشی را انتخاب کنید.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@button_editor_context_router.callback_query(F.data.startswith("uiv4:cat:"))
async def category_menu(callback: CallbackQuery, state: FSMContext):
    if not is_staff(callback.from_user.id):
        await callback.answer("غیرمجاز", show_alert=True)
        return
    await state.clear()
    parts = (callback.data or "").split(":")
    if len(parts) != 4:
        await callback.answer("بخش نامعتبر", show_alert=True)
        return
    scope, category = parts[2], parts[3]
    if scope not in SCOPE_META or category not in CATEGORY_META:
        await callback.answer("بخش نامعتبر", show_alert=True)
        return

    classified = await _classified_items()
    screen_counts: dict[str, int] = {}
    screen_defs: dict[str, ScreenDef] = {}
    for _logical, screen, _item in classified:
        if screen.scope == scope and screen.category == category:
            screen_counts[screen.key] = screen_counts.get(screen.key, 0) + 1
            screen_defs[screen.key] = screen

    ordered = sorted(screen_defs.values(), key=lambda s: (s.rank, s.title))
    rows = [
        [await _btn(f"{screen.title} · {screen_counts[screen.key]}", f"uiv4:screen:{screen.key}:0")]
        for screen in ordered
    ]
    rows.append([await _btn("بازگشت به بخش‌ها", f"uiv4:scope:{scope}", fallback="⬅️")])

    title, emoji = CATEGORY_META[category]
    await callback.message.edit_text(
        f"{emoji} <b>{title}</b>\n\n"
        "صفحه‌ای را که دکمه داخل آن دیده می‌شود انتخاب کنید.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@button_editor_context_router.callback_query(F.data.startswith("uiv4:screen:"))
async def screen_menu(callback: CallbackQuery, state: FSMContext):
    if not is_staff(callback.from_user.id):
        await callback.answer("غیرمجاز", show_alert=True)
        return
    await state.clear()
    parts = (callback.data or "").split(":")
    if len(parts) < 4:
        await callback.answer("صفحه نامعتبر", show_alert=True)
        return
    try:
        page = max(0, int(parts[-1]))
    except ValueError:
        page = 0
    screen_key = ":".join(parts[2:-1])
    screen = screen_for(screen_key)
    if screen is None:
        await callback.answer("صفحه نامعتبر", show_alert=True)
        return

    items = [
        (logical, item)
        for logical, item_screen, item in await _classified_items()
        if item_screen.key == screen_key
    ]
    items.sort(key=lambda pair: (pair[0].rank, pair[0].title, pair[1].id))
    page_size = 9
    pages = max(1, math.ceil(len(items) / page_size))
    page = min(page, pages - 1)
    visible = items[page * page_size:(page + 1) * page_size]

    rows = []
    for logical, item in visible:
        current = _current_label(item)
        changed = current != _canonical_label(item.default_text)
        emoji = f" · ✨ {item.emoji_key}" if item.emoji_key else ""
        marker = "✏️ " if changed else ""
        label = f"{marker}{logical.title} | {current}{emoji}"
        rows.append([await _btn(label[:60], f"pui:b:{item.id}", fallback="🔘")])

    nav = []
    if page:
        nav.append(await _btn("قبلی", f"uiv4:screen:{screen_key}:{page-1}", fallback="⬅️"))
    if page + 1 < pages:
        nav.append(await _btn("بعدی", f"uiv4:screen:{screen_key}:{page+1}", fallback="➡️"))
    if nav:
        rows.append(nav)
    rows.append([await _btn("بازگشت به صفحات", f"uiv4:cat:{screen.scope}:{screen.category}", fallback="⬅️")])

    await callback.message.edit_text(
        f"🔘 <b>{screen.title}</b>\n\n"
        "سمت چپ نام کاربرد دکمه است و بعد از | متن فعلی آن را می‌بینید.\n"
        "روی دکمه بزنید تا متن یا Premium Emoji را تغییر دهید.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


# Redirect only retired *button* editor callbacks. The uiv2:mc:, uiv2:m:,
# uiv2:me:, uiv2:save, etc. namespace is still used by the live TEXT editor;
# catching every uiv2: callback here hijacks the message editor into buttons.
@button_editor_context_router.callback_query(F.data.startswith("uiv2:bc:"))
@button_editor_context_router.callback_query(F.data.startswith("uiv3:"))
async def legacy_editor_redirect(callback: CallbackQuery, state: FSMContext):
    if not is_staff(callback.from_user.id):
        await callback.answer("غیرمجاز", show_alert=True)
        return
    await state.clear()
    await _render_root(callback.message)
    await callback.answer("ساختار مدیریت دکمه‌ها به‌روزرسانی شد")