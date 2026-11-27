"""Fastly storefront and staff console; callback namespace cannot overlap panel sales."""
from __future__ import annotations

import io
import logging
from html import escape

from aiogram import F, Router
from aiogram.dispatcher.middlewares.base import BaseMiddleware
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import config
from authorization import is_staff
from database import db
from fastly_store_service import FastlyStoreError, fastly_store
from premium_ui_service import premium_ui_service
from style_engine import style_engine
from trust_service import trust_service

logger = logging.getLogger(__name__)
fastly_router = Router(name="fastly_store")


class FastlyPrivateMiddleware(BaseMiddleware):
    """No credentials or payment steps can execute from groups/channels."""
    async def __call__(self, handler, event, data):
        chat = getattr(getattr(event, "message", None), "chat", None)
        if getattr(chat, "type", None) != "private" or int(getattr(chat, "id", 0)) != int(event.from_user.id):
            await event.answer("این بخش فقط در گفتگوی خصوصی ربات قابل استفاده است.", show_alert=True)
            return None
        return await handler(event, data)


fastly_router.callback_query.outer_middleware(FastlyPrivateMiddleware())

REASONS = {
    "extra": "اکانت بیشتری می‌خواهم",
    "blocked": "اکانت قبلی مسدود شده",
    "login": "مشکل ورود دارم",
    "quality": "اکانت قبلی مناسب نیازم نبود",
    "other": "دلیل دیگری دارم",
}
CLAIM_REASONS = {key: value for key, value in REASONS.items() if key != "extra"}


class FastlyStates(StatesGroup):
    manual_quantity = State()
    receipt = State()
    stock = State()
    product_title = State()
    product_price = State()
    product_days = State()
    product_replacements = State()
    product_rules = State()
    edit_product = State()
    claim_note = State()


def _uid(event) -> int:
    return int(event.from_user.id)


def _staff(event) -> bool:
    return is_staff(_uid(event))


async def _button(text: str, cb: str, emoji: str = "🛍"):
    return await style_engine.styled_button(text, callback_data=cb, fallback=emoji)


def _kb(rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _message(key: str, **values) -> str:
    raw = str(config.MESSAGES.get(key, "")).strip()
    if not raw:
        raise FastlyStoreError("قالب پیام تعریف نشده است.")
    return await premium_ui_service.render_placeholders(raw.format(**values))


def _error(exc: Exception) -> str:
    if isinstance(exc, FastlyStoreError):
        return escape(str(exc))
    logger.exception("Fastly operation failed: %s", type(exc).__name__)
    return "عملیات انجام نشد؛ دوباره تلاش کنید یا به پشتیبانی پیام بدهید."


async def _home(message: Message):
    if not fastly_store.is_enabled():
        await message.edit_text("فروشگاه اکانت‌ها فعلاً غیرفعال است.", reply_markup=_kb([
            [await _button("بازگشت", "back_to_admin_main", "↩️")],
        ]))
        return
    try:
        fastly_store._cipher()
        products = await fastly_store.products()
    except Exception as exc:
        await message.edit_text(_error(exc), reply_markup=_kb([
            [await _button("بازگشت", "back_to_admin_main", "↩️")],
        ]))
        return
    rows = [
        [await _button(f"{p['title']} · {p['price_toman']:,} تومان · {p['stock']} عدد",
                       f"fastly:product:{p['id']}", "📦")]
        for p in products if p["stock"] > 0
    ]
    if not rows:
        rows = [[await _button("سفارش‌های من", "fastly:orders", "🧾")]]
    else:
        rows.append([await _button("سفارش‌های من", "fastly:orders", "🧾")])
    rows.append([await _button("درخواست تعویض", "fastly:claimitems", "🛡")])
    rows.append([await _button("بازگشت به صفحه اصلی", "back_to_admin_main", "↩️")])
    await message.edit_text(
        await _message("fastly_home"), reply_markup=_kb(rows)
    )


@fastly_router.callback_query(F.data == "fastly:home")
async def home(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await _home(callback.message)
    await callback.answer()


@fastly_router.callback_query(F.data.startswith("fastly:product:"))
async def show_product(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        product = await fastly_store.product(int(callback.data.rsplit(":",1)[1]))
        if not product or not product["enabled"]:
            raise FastlyStoreError("محصول در دسترس نیست.")
        text = await _message(
            "fastly_product", title=escape(product["title"]),
            description=escape(product["description"] or "اکانت آماده تحویل"),
            price=f"{product['price_toman']:,}",
            stock=int(product["stock"]),
            warranty=(f"{product['warranty_days']} روز / {product['warranty_replacements']} تعویض"
                      if product["warranty_replacements"] else "بدون ضمانت تعویض"),
        )
        await callback.message.edit_text(text, reply_markup=_kb([
            [await _button("انتخاب تعداد", f"fastly:qty:{product['id']}", "🛒")],
            [await _button("بازگشت", "fastly:home", "↩️")],
        ]))
    except Exception as exc:
        await callback.answer(_error(exc), show_alert=True)
        return
    await callback.answer()


@fastly_router.callback_query(F.data.startswith("fastly:qty:"))
async def select_quantity(callback: CallbackQuery):
    product = await fastly_store.product(int(callback.data.rsplit(":",1)[1]))
    if not product or not product["enabled"] or product["stock"] < 1:
        await callback.answer("موجودی تمام شده است.", show_alert=True)
        return
    allowed = min(product["stock"], product["max_quantity"])
    options = [
        await _button(f"{n} عدد", f"fastly:choose:{product['id']}:{n}", "📦")
        for n in range(1, min(allowed, 6)+1)
    ]
    rows = [options[i:i+3] for i in range(0,len(options),3)]
    if allowed > 6:
        rows.append([await _button("تعداد دلخواه", f"fastly:qtycustom:{product['id']}", "✏️")])
    rows.append([await _button("بازگشت", f"fastly:product:{product['id']}", "↩️")])
    await callback.message.edit_text(
        f"<b>{escape(product['title'])}</b>\n"
        f"موجودی: {product['stock']} عدد\nچند اکانت می‌خواهید؟",
        reply_markup=_kb(rows),
    )
    await callback.answer()


@fastly_router.callback_query(F.data.startswith("fastly:qtycustom:"))
async def custom_quantity(callback: CallbackQuery, state: FSMContext):
    product_id = int(callback.data.rsplit(":",1)[1])
    product = await fastly_store.product(product_id)
    if not product or not product["enabled"]:
        await callback.answer("محصول موجود نیست.", show_alert=True)
        return
    await state.set_state(FastlyStates.manual_quantity)
    await state.update_data(fastly_product_id=product_id)
    await callback.message.answer(
        f"تعداد موردنظر را به عدد بنویس (از ۱ تا {min(product['stock'],product['max_quantity'])})."
    )
    await callback.answer()


@fastly_router.message(FastlyStates.manual_quantity)
async def custom_quantity_value(message: Message, state: FSMContext):
    data = await state.get_data()
    try:
        qty = int(str(message.text or ""))
        product = await fastly_store.product(int(data["fastly_product_id"]))
        if not product or not 1 <= qty <= min(product["stock"],product["max_quantity"]):
            raise ValueError
    except (ValueError, TypeError, KeyError):
        await message.answer("تعداد معتبر نیست. دوباره به عدد بفرست یا /start را بزن.")
        return
    await state.clear()
    await _choose(message, int(data["fastly_product_id"]), qty, message.from_user.id)


async def _choose(message: Message, product_id: int, quantity: int, user_id: int):
    if await fastly_store.has_purchases(user_id):
        rows = [
            [await _button(title, f"fastly:reason:{product_id}:{quantity}:{code}", "🔹")]
            for code, title in REASONS.items()
        ]
        rows.append([await _button("بازگشت", "fastly:home", "↩️")])
        await message.answer(
            "برای ثبت دقیق آمار، دلیل خرید دوباره رو انتخاب کن:",
            reply_markup=_kb(rows),
        )
    else:
        try:
            order_id = await fastly_store.make_order(user_id, product_id, quantity, "first")
            await _order_screen(message, order_id, user_id, edit=False)
        except Exception as exc:
            await message.answer(_error(exc))


@fastly_router.callback_query(F.data.startswith("fastly:choose:"))
async def choose(callback: CallbackQuery):
    _,_,pid,qty = callback.data.split(":")
    await _choose(callback.message, int(pid), int(qty), _uid(callback))
    await callback.answer()


@fastly_router.callback_query(F.data.startswith("fastly:reason:"))
async def choose_reason(callback: CallbackQuery):
    _,_,pid,qty,reason = callback.data.split(":")
    try:
        order_id = await fastly_store.make_order(_uid(callback),int(pid),int(qty),reason)
        await _order_screen(callback.message, order_id, _uid(callback), edit=True)
    except Exception as exc:
        await callback.answer(_error(exc), show_alert=True)
        return
    await callback.answer()


async def _order_screen(message: Message, order_id: int, buyer: int, edit: bool = True):
    order = await fastly_store.order(order_id,buyer=buyer)
    if not order:
        raise FastlyStoreError("سفارش پیدا نشد.")
    states = {"pending": "در انتظار پرداخت", "submitted": "رسید در حال بررسی",
              "allocated": "آماده دریافت", "out_of_stock": "تأیید پرداخت؛ کمبود موجودی",
              "rejected": "رد شده"}
    text = await _message(
        "fastly_order", order_id=order["id"], title=escape(order["title_snapshot"]),
        quantity=order["quantity"], total=f"{order['total_toman']:,}",
        status=states.get(order["state"], "نامشخص"),
    )
    rows = []
    if order["state"] == "pending":
        rows.append([await _button("ادامه پرداخت",f"fastly:pay:{order_id}","💳")])
    if order["state"] == "allocated":
        rows.append([await _button("قوانین و دریافت اکانت‌ها",f"fastly:rules:{order_id}","🔐")])
    if order["state"] == "out_of_stock":
        text += "\n\nپرداخت ثبت شده؛ قبل از تحویل، مدیریت باید موجودی یا بازپرداخت را بررسی کند."
    rows.append([await _button("سفارش‌های من","fastly:orders","🧾")])
    rows.append([await _button("فروشگاه","fastly:home","↩️")])
    if edit:
        await message.edit_text(text,reply_markup=_kb(rows))
    else:
        await message.answer(text,reply_markup=_kb(rows))


@fastly_router.callback_query(F.data == "fastly:orders")
async def my_orders(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    orders = await fastly_store.orders(buyer=_uid(callback),limit=20)
    rows = [
        [await _button(f"#{o['id']} · {o['title_snapshot'][:30]} · {o['quantity']} عدد",
                       f"fastly:order:{o['id']}","🧾")]
        for o in orders
    ]
    rows.append([await _button("بازگشت","fastly:home","↩️")])
    await callback.message.edit_text(
        "🧾 <b>سفارش‌های اکانت</b>\n\n" +
        (f"تعداد سفارش‌ها در این صفحه: {len(orders)}" if orders else "هنوز سفارشی ثبت نکردی."),
        reply_markup=_kb(rows),
    )
    await callback.answer()


@fastly_router.callback_query(F.data.startswith("fastly:order:"))
async def order_details(callback: CallbackQuery):
    try:
        await _order_screen(callback.message,int(callback.data.rsplit(":",1)[1]),_uid(callback))
    except Exception as exc:
        await callback.answer(_error(exc),show_alert=True)
        return
    await callback.answer()


@fastly_router.callback_query(F.data.startswith("fastly:pay:"))
async def pay(callback: CallbackQuery):
    ident = int(callback.data.rsplit(":",1)[1])
    order = await fastly_store.order(ident,buyer=_uid(callback))
    if not order or order["state"] != "pending":
        await callback.answer("این سفارش دیگر در انتظار پرداخت نیست.",show_alert=True)
        return
    if not await trust_service.is_identity_approved(_uid(callback)):
        await callback.message.edit_text(
            await _message("fastly_kyc"),
            reply_markup=_kb([
                [await _button("شروع / ادامه احراز هویت","kyc:start","🪪")],
                [await _button("وضعیت احراز هویت","kyc:status","🔎")],
                [await _button("سفارش‌های من","fastly:orders","🧾")],
            ]),
        )
        await callback.answer()
        return
    cards = await db.get_cards(only_active=True)
    if not cards:
        await callback.answer("در حال حاضر کارت پرداخت فعال نداریم.",show_alert=True)
        return
    rows = [
        f"• <b>{escape(str(c.get('bank_name') or 'بانک'))}</b> "
        f"<code>{escape(str(c.get('card_number') or ''))}</code> "
        f"({escape(str(c.get('holder_name') or ''))})"
        for c in cards
    ]
    msg = await _message(
        "fastly_payment", order_id=order["id"], total=f"{order['total_toman']:,}",
        cards="\n".join(rows),
    )
    await callback.message.edit_text(msg,reply_markup=_kb([
        [await _button("پرداخت کردم · ارسال رسید",f"fastly:receipt:{ident}","🧾")],
        [await _button("بازگشت",f"fastly:order:{ident}","↩️")],
    ]))
    await callback.answer()


@fastly_router.callback_query(F.data.startswith("fastly:receipt:"))
async def send_receipt(callback: CallbackQuery,state: FSMContext):
    ident = int(callback.data.rsplit(":",1)[1])
    order = await fastly_store.order(ident,buyer=_uid(callback))
    if not order or order["state"] != "pending":
        await callback.answer("این سفارش منتظر رسید نیست.",show_alert=True)
        return
    if not await trust_service.is_identity_approved(_uid(callback)):
        await callback.answer("ابتدا احراز هویت را تکمیل کن.",show_alert=True)
        return
    await state.set_state(FastlyStates.receipt)
    await state.update_data(fastly_order_id=ident)
    await callback.message.answer("عکس رسید رو همین‌جا بفرست.\nلطفاً اطلاعات حساس اضافی روی تصویر نباشه.")
    await callback.answer()


@fastly_router.message(FastlyStates.receipt)
async def receipt_value(message: Message,state:FSMContext):
    if not message.photo:
        await message.answer("برای ثبت پرداخت، عکس رسید رو به‌صورت Photo بفرست.")
        return
    ident = int((await state.get_data()).get("fastly_order_id",0))
    if not await trust_service.is_identity_approved(_uid(message)):
        await state.clear()
        await message.answer("برای ادامه، احراز هویت باید تأیید شده باشه.")
        return
    try:
        ok = await fastly_store.submit_receipt(ident,_uid(message),message.photo[-1].file_id)
        if not ok:
            raise FastlyStoreError("سفارش قبلاً بررسی شده یا متعلق به شما نیست.")
    except Exception as exc:
        await message.answer(_error(exc))
        return
    await state.clear()
    await message.answer(await _message("fastly_receipt_received",order_id=ident))
    for owner in set(int(x) for x in config.SUDO_ADMINS):
        try:
            await message.bot.send_photo(
                owner,message.photo[-1].file_id,
                caption=f"🧾 <b>رسید خرید اکانت Fastly</b>\n"
                        f"سفارش <code>#{ident}</code> · مشتری <code>{_uid(message)}</code>\n"
                        "قبل از تأیید، مبلغ و کارت مبدأ را با تراکنش واقعی تطبیق بده.",
                reply_markup=_kb([
                    [await _button("تأیید و تخصیص",f"fastlyadmin:approve:{ident}","✅"),
                     await _button("رد رسید",f"fastlyadmin:reject:{ident}","❌")],
                ]),
            )
        except Exception:
            logger.warning("Fastly receipt delivery failed to owner ID %s",owner)


@fastly_router.callback_query(F.data.startswith("fastly:rules:"))
async def show_rules(callback: CallbackQuery):
    ident = int(callback.data.rsplit(":",1)[1])
    order = await fastly_store.order(ident,buyer=_uid(callback))
    if not order or order["state"] != "allocated":
        await callback.answer("این سفارش آماده تحویل نیست.",show_alert=True)
        return
    text = await _message(
        "fastly_rules", order_id=ident, rules=escape(order["rules_snapshot"]),
    )
    await callback.message.edit_text(text,reply_markup=_kb([
        [await _button("قوانین را خواندم · دریافت اکانت‌ها",f"fastly:accept:{ident}","✅")],
        [await _button("بازگشت",f"fastly:order:{ident}","↩️")],
    ]))
    await callback.answer()


@fastly_router.callback_query(F.data.startswith("fastly:accept:"))
async def accept_rules(callback: CallbackQuery):
    ident = int(callback.data.rsplit(":",1)[1])
    if not await fastly_store.accept_rules(ident,_uid(callback)):
        await callback.answer("سفارش معتبر نیست.",show_alert=True)
        return
    await _send_credentials(callback.message,ident,_uid(callback),edit=True)
    await callback.answer()


async def _send_credentials(message:Message,ident:int,buyer:int,*,edit:bool):
    if str(getattr(message.chat, "type", "")) != "private" or int(message.chat.id) != int(buyer):
        await message.answer("تحویل اکانت فقط در گفتگوی خصوصی خریدار انجام می‌شود.")
        return
    try:
        rows = await fastly_store.reveal(ident,buyer)
    except Exception as exc:
        await message.answer(_error(exc))
        return
    # Do not include passwords in admin groups, logs, callback data or analytics.
    header = f"🔐 <b>اکانت‌های سفارش #{ident}</b>\n"
    parts = [
        f"\n<b>{i}.</b> ایمیل: <code>{escape(row['login'])}</code>\n"
        f"رمز عبور: <code>{escape(row['password'])}</code>"
        for i,row in enumerate(rows,1)
    ]
    text = header+"\n".join(parts)+"\n\nاین اطلاعات محرمانه‌اند؛ در اختیار دیگران قرار نده."
    try:
        # Telegram private bot conversations are not end-to-end encrypted.
        if len(text)>3900:
            await message.answer(header+"تعداد اکانت‌ها زیاد است؛ آن‌ها را در پیام‌های جدا می‌فرستم.")
            for start in range(0,len(parts),8):
                await message.answer("\n".join(parts[start:start+8]))
        else:
            await message.answer(text)
        await fastly_store.mark_revealed(ident,buyer)
    except Exception:
        logger.warning("Fastly buyer delivery failed, order=%s",ident)
        await message.answer("ارسال اکانت‌ها کامل نشد؛ از «سفارش‌های من» دوباره دریافت کن.")
        return
    await message.answer(
        "برای مشاهده دوباره، از «سفارش‌های من» استفاده کن. "
        "این گفتگو در تلگرام رمزگذاری سرتاسری ندارد.",
        reply_markup=_kb([
            [await _button("سفارش‌های من","fastly:orders","🧾")],
            [await _button("درخواست تعویض","fastly:claimitems","🛡")],
        ]),
    )


@fastly_router.callback_query(F.data == "fastly:claimitems")
async def claimable(callback: CallbackQuery):
    items = await fastly_store.claimable_items(_uid(callback))
    rows = [
        [await _button(f"سفارش #{i['order_id']} · {i['title_snapshot'][:25]} · واحد #{i['id']}",
                       f"fastly:claimitem:{i['id']}","🛡")]
        for i in items
    ]
    rows.append([await _button("بازگشت","fastly:home","↩️")])
    await callback.message.edit_text(
        "🛡 <b>درخواست تعویض ضمانتی</b>\n\n"
        + ("یک اکانت رو انتخاب کن:" if items else "اکانتی با ضمانت فعال برای تعویض پیدا نشد."),
        reply_markup=_kb(rows),
    )
    await callback.answer()


@fastly_router.callback_query(F.data.startswith("fastly:claimitem:"))
async def claim_reason_menu(callback: CallbackQuery):
    ident = int(callback.data.rsplit(":",1)[1])
    if ident not in {int(x["id"]) for x in await fastly_store.claimable_items(_uid(callback))}:
        await callback.answer("این اکانت قابل تعویض نیست.",show_alert=True)
        return
    rows = [
        [await _button(label,f"fastly:claim:{ident}:{reason}","📝")]
        for reason,label in CLAIM_REASONS.items()
    ]
    rows.append([await _button("بازگشت","fastly:claimitems","↩️")])
    await callback.message.edit_text("دلیل درخواست تعویض رو انتخاب کن:",reply_markup=_kb(rows))
    await callback.answer()


async def _create_claim(message:Message,buyer:int,item_id:int,reason:str,note:str=""):
    try:
        claim_id = await fastly_store.claim(buyer,item_id,reason,note)
        await message.answer(await _message("fastly_claim_created",claim_id=claim_id))
        for owner in set(int(x) for x in config.SUDO_ADMINS):
            try:
                await message.bot.send_message(
                    owner, f"🛡 درخواست تعویض #{claim_id}\nمشتری <code>{buyer}</code> · واحد #{item_id}\n"
                           f"علت: {escape(CLAIM_REASONS.get(reason,'سایر'))}",
                    reply_markup=_kb([
                        [await _button("تأیید تعویض",f"fastlyadmin:claimyes:{claim_id}","✅"),
                         await _button("رد درخواست",f"fastlyadmin:claimno:{claim_id}","❌")],
                    ]),
                )
            except Exception:
                logger.warning("Fastly claim notification failed to owner %s",owner)
    except Exception as exc:
        await message.answer(_error(exc))


@fastly_router.callback_query(F.data.startswith("fastly:claim:"))
async def claim_select(callback: CallbackQuery,state:FSMContext):
    _,_,item,reason = callback.data.split(":")
    if reason not in CLAIM_REASONS:
        await callback.answer("دلیل معتبر نیست.",show_alert=True);return
    if reason=="other":
        await state.set_state(FastlyStates.claim_note)
        await state.update_data(item_id=int(item),reason=reason)
        await callback.message.answer("مشکل رو کوتاه بنویس (حداکثر ۵۰۰ نویسه):")
    else:
        await _create_claim(callback.message,_uid(callback),int(item),reason)
    await callback.answer()


@fastly_router.message(FastlyStates.claim_note)
async def claim_note_value(message:Message,state:FSMContext):
    note = str(message.text or "").strip()
    if not 1 <= len(note) <= 500:
        await message.answer("توضیح باید حداکثر ۵۰۰ نویسه و غیرخالی باشه.")
        return
    data=await state.get_data()
    await state.clear()
    await _create_claim(message,_uid(message),int(data["item_id"]),str(data["reason"]),note)


# ------------------------------ Staff console ------------------------------

async def _staff_guard(callback:CallbackQuery)->bool:
    if _staff(callback):
        return True
    await callback.answer("دسترسی ندارید.",show_alert=True)
    return False


async def _admin_home(message:Message):
    stats=await fastly_store.stats()
    rows=[
        [await _button("محصولات و موجودی","fastlyadmin:products","📦")],
        [await _button("افزودن محصول","fastlyadmin:new","➕")],
        [await _button("رسیدهای در انتظار","fastlyadmin:orders","🧾")],
        [await _button("درخواست‌های تعویض","fastlyadmin:claims","🛡")],
        [await _button("آمار فروش","fastlyadmin:stats","📊")],
        [await _button("بازگشت به فروش و سفارش‌ها","ops:hub:sales","↩️")],
    ]
    await message.edit_text(
        "🛍 <b>مدیریت فروشگاه Fastly</b>\n\n"
        f"📦 موجودی آزاد: {stats['available']}\n"
        f"🧾 رسیدهای منتظر بررسی: {stats['pending']}\n"
        f"🛡 درخواست تعویض باز: {stats['claims_pending']}",
        reply_markup=_kb(rows),
    )


@fastly_router.message(Command("fastly_admin"))
async def admin_command(message:Message):
    if not _staff(message) or message.chat.type != "private":
        return
    stats=await fastly_store.stats()
    await message.answer(
        f"🛍 مدیریت Fastly\nموجودی: {stats['available']} · رسیدهای معلق: {stats['pending']}",
        reply_markup=_kb([[await _button("مرکز مدیریت","fastlyadmin:home","⚙️")]]),
    )


@fastly_router.callback_query(F.data == "fastlyadmin:home")
async def admin_home(callback:CallbackQuery,state:FSMContext):
    if not await _staff_guard(callback):return
    await state.clear()
    await _admin_home(callback.message)
    await callback.answer()


@fastly_router.callback_query(F.data == "fastlyadmin:products")
async def admin_products(callback:CallbackQuery):
    if not await _staff_guard(callback):return
    products=await fastly_store.products(False)
    rows=[
        [await _button(
            f"#{p['id']} · {p['title'][:30]} · {p['stock']} موجود",
            f"fastlyadmin:item:{p['id']}","📦")]
        for p in products
    ]
    rows.append([await _button("افزودن محصول","fastlyadmin:new","➕")])
    rows.append([await _button("بازگشت","fastlyadmin:home","↩️")])
    await callback.message.edit_text(
        "📦 <b>محصولات فروشگاه</b>\n\n"
        + ("مدل موردنظر رو انتخاب کن:" if products else "هنوز محصولی نداری."),
        reply_markup=_kb(rows),
    )
    await callback.answer()


@fastly_router.callback_query(F.data.startswith("fastlyadmin:item:"))
async def admin_item(callback:CallbackQuery):
    if not await _staff_guard(callback):return
    ident=int(callback.data.rsplit(":",1)[1])
    product=await fastly_store.product(ident)
    if not product:
        await callback.answer("محصول پیدا نشد.",show_alert=True);return
    fields=[
        ("title","نام محصول"),("price_toman","قیمت"),
        ("warranty_days","روز ضمانت"),("warranty_replacements","تعداد تعویض"),
        ("max_quantity","سقف خرید"),("rules","قوانین استفاده"),
    ]
    rows=[
        [await _button(f"ویرایش {title}",f"fastlyadmin:edit:{ident}:{field}","✏️")]
        for field,title in fields
    ]
    rows += [
        [await _button("ورود اکانت‌ها",f"fastlyadmin:stock:{ident}","🔐")],
        [await _button("غیرفعال کردن" if product["enabled"] else "فعال کردن",
                       f"fastlyadmin:toggle:{ident}","🔄")],
        [await _button("بازگشت","fastlyadmin:products","↩️")],
    ]
    warranty=f"{product['warranty_days']} روز · {product['warranty_replacements']} تعویض"
    await callback.message.edit_text(
        f"📦 <b>{escape(product['title'])}</b>\n\n"
        f"موجودی: <b>{product['stock']}</b>\n"
        f"قیمت: <b>{product['price_toman']:,}</b> تومان\n"
        f"ضمانت: {warranty}\n"
        f"سقف خرید: {product['max_quantity']} عدد\n"
        f"وضعیت: {'فعال' if product['enabled'] else 'غیرفعال'}",
        reply_markup=_kb(rows),
    )
    await callback.answer()


@fastly_router.callback_query(F.data.startswith("fastlyadmin:toggle:"))
async def toggle_product(callback:CallbackQuery):
    if not await _staff_guard(callback):return
    ident=int(callback.data.rsplit(":",1)[1])
    product=await fastly_store.product(ident)
    if not product:
        await callback.answer("محصول موجود نیست.",show_alert=True);return
    await fastly_store.edit_product(ident,"enabled",0 if product["enabled"] else 1,_uid(callback))
    await callback.answer("ذخیره شد.")
    await _admin_home(callback.message)


@fastly_router.callback_query(F.data == "fastlyadmin:new")
async def new_product(callback:CallbackQuery,state:FSMContext):
    if not await _staff_guard(callback):return
    await state.clear()
    await state.set_state(FastlyStates.product_title)
    await callback.message.answer("اسم محصول رو بنویس. مثلاً: فستلی تازه‌ساخت — XHTTP باز")
    await callback.answer()


@fastly_router.message(FastlyStates.product_title)
async def product_title(message:Message,state:FSMContext):
    if not _staff(message):return
    name=str(message.text or "").strip()
    if not 2 <= len(name) <= 100:
        await message.answer("نام محصول باید ۲ تا ۱۰۰ نویسه باشه.");return
    await state.update_data(title=name)
    await state.set_state(FastlyStates.product_price)
    await message.answer("قیمت هر اکانت رو به تومان و به عدد وارد کن.")


@fastly_router.message(FastlyStates.product_price)
async def product_price(message:Message,state:FSMContext):
    if not _staff(message):return
    try:
        value=int((message.text or "").replace(",",""))
        if not 1 <= value <= 2_000_000_000:raise ValueError
    except ValueError:
        await message.answer("قیمت معتبر نیست. فقط عدد مثبت به تومان.");return
    await state.update_data(price=value)
    await state.set_state(FastlyStates.product_days)
    await message.answer("مدت ضمانت چند روزه؟ برای محصول عادی عدد ۰ بفرست.")


@fastly_router.message(FastlyStates.product_days)
async def product_days(message:Message,state:FSMContext):
    if not _staff(message):return
    try:
        value=int(message.text or "")
        if not 0 <= value <= 3650:raise ValueError
    except ValueError:
        await message.answer("یک عدد بین ۰ و ۳۶۵۰ بفرست.");return
    await state.update_data(days=value)
    await state.set_state(FastlyStates.product_replacements)
    await message.answer("حداکثر تعداد تعویض در مدت ضمانت؟ برای بدون ضمانت ۰ بفرست.")


@fastly_router.message(FastlyStates.product_replacements)
async def product_replacements(message:Message,state:FSMContext):
    if not _staff(message):return
    try:
        value=int(message.text or "")
        if not 0 <= value <= 100:raise ValueError
    except ValueError:
        await message.answer("یک عدد بین ۰ تا ۱۰۰ بفرست.");return
    data=await state.get_data()
    if value>0 and not data.get("days"):
        await message.answer("برای تعویض باید مدت ضمانت بیشتر از صفر باشه.");return
    await state.update_data(replacements=value)
    await state.set_state(FastlyStates.product_rules)
    await message.answer("متن قوانین استفاده و شرایط تعویض رو بفرست. این متن هنگام خرید ذخیره می‌شه.")


@fastly_router.message(FastlyStates.product_rules)
async def product_rules(message:Message,state:FSMContext):
    if not _staff(message):return
    rules=str(message.text or "").strip()
    if not 1 <= len(rules) <= 3500:
        await message.answer("متن قوانین باید بین ۱ تا ۳۵۰۰ نویسه باشه.");return
    data=await state.get_data()
    try:
        ident=await fastly_store.add_product(data["title"],data["price"],data["days"],
                                            data["replacements"],rules,_uid(message))
    except Exception as exc:
        await message.answer(_error(exc));return
    await state.clear()
    await message.answer(
        f"✅ محصول #{ident} ساخته شد. حالا از بخش محصولات، اکانت‌هاش رو وارد کن.",
        reply_markup=_kb([[await _button("محصولات","fastlyadmin:products","📦")]]),
    )


@fastly_router.callback_query(F.data.startswith("fastlyadmin:edit:"))
async def edit_product(callback:CallbackQuery,state:FSMContext):
    if not await _staff_guard(callback):return
    _,_,pid,field=callback.data.split(":")
    if field not in {"title","price_toman","warranty_days","warranty_replacements","max_quantity","rules"}:
        await callback.answer("فیلد نامعتبر است.",show_alert=True);return
    if not await fastly_store.product(int(pid)):
        await callback.answer("محصول وجود ندارد.",show_alert=True);return
    await state.clear()
    await state.set_state(FastlyStates.edit_product)
    await state.update_data(pid=int(pid),field=field)
    await callback.message.answer("مقدار جدید رو بفرست. برای انصراف /start رو بزن.")
    await callback.answer()


@fastly_router.message(FastlyStates.edit_product)
async def edit_product_value(message:Message,state:FSMContext):
    if not _staff(message):return
    data=await state.get_data()
    try:
        await fastly_store.edit_product(data["pid"],data["field"],message.text or "",_uid(message))
    except Exception as exc:
        await message.answer(_error(exc));return
    await state.clear()
    await message.answer("✅ تغییر ذخیره شد.",reply_markup=_kb([
        [await _button("محصولات","fastlyadmin:products","📦")]
    ]))


@fastly_router.callback_query(F.data.startswith("fastlyadmin:stock:"))
async def stock_start(callback:CallbackQuery,state:FSMContext):
    if not await _staff_guard(callback):return
    ident=int(callback.data.rsplit(":",1)[1])
    if not await fastly_store.product(ident):
        await callback.answer("محصول پیدا نشد.",show_alert=True);return
    await state.clear()
    await state.set_state(FastlyStates.stock)
    await state.update_data(pid=ident)
    await callback.message.answer(
        "🔐 اکانت‌ها رو در یک پیام خصوصی یا فایل TXT وارد کن.\n"
        "هر خط: <code>email@example.com|password</code>\n"
        "حداکثر ۲۰۰ مورد در هر نوبت. اکانت تکراری ذخیره نمی‌شه.\n\n"
        "⚠️ پیام به بات در تلگرام رمزگذاری سرتاسری نداره. بعد از واردکردن، "
        "ربات تلاش می‌کنه پیام حاوی رمز رو حذف کنه."
    )
    await callback.answer()


@fastly_router.message(FastlyStates.stock)
async def stock_value(message:Message,state:FSMContext):
    if not _staff(message):return
    if message.chat.type != "private":
        await message.answer("اطلاعات اکانت رو فقط در گفتگوی خصوصی وارد کن.");return
    try:
        if message.document:
            doc=message.document
            if not doc.file_name or not doc.file_name.lower().endswith((".txt",".csv")) or (doc.file_size or 0)>100_000:
                raise FastlyStoreError("فقط فایل TXT یا CSV حداکثر ۱۰۰ کیلوبایت.")
            stream=io.BytesIO()
            await message.bot.download(doc,destination=stream)
            content=stream.getvalue().decode("utf-8-sig")
        elif message.text:
            content=message.text
        else:
            raise FastlyStoreError("متن یا فایل TXT بفرست.")
        lines=[x.rstrip("\r") for x in content.splitlines() if x.strip()]
        if not 1<=len(lines)<=200:
            raise FastlyStoreError("تعداد سطرها باید بین ۱ تا ۲۰۰ باشه.")
        pairs=[]
        for idx,line in enumerate(lines,1):
            if "|" not in line:
                raise FastlyStoreError(f"قالب سطر {idx} درست نیست. از email|password استفاده کن.")
            login,pwd=line.split("|",1)
            pairs.append((login,pwd))
        product_id=int((await state.get_data())["pid"])
        added,skipped=await fastly_store.add_stock(product_id,pairs,_uid(message))
    except Exception as exc:
        await message.answer(_error(exc))
        return
    finally:
        if message.text or message.document:
            try:await message.delete()
            except Exception:pass
    await state.clear()
    await message.answer(
        f"✅ {added} اکانت افزوده شد. {skipped} سطر تکراری یا نامعتبر بود.\n"
        "اطلاعات رمزها در این بخش نمایش داده نمی‌شن.",
        reply_markup=_kb([[await _button("مدیریت محصولات","fastlyadmin:products","📦")]]),
    )


@fastly_router.callback_query(F.data == "fastlyadmin:orders")
async def pending_orders(callback:CallbackQuery):
    if not await _staff_guard(callback):return
    orders=await fastly_store.orders(state="submitted")
    rows=[
        [await _button(f"#{o['id']} · {o['quantity']} عدد · {o['total_toman']:,} تومان",
                       f"fastlyadmin:review:{o['id']}","🧾")]
        for o in orders
    ]
    rows.append([await _button("بازگشت","fastlyadmin:home","↩️")])
    await callback.message.edit_text(
        "🧾 <b>رسیدهای Fastly منتظر بررسی</b>\n\n"
        + (f"{len(orders)} سفارش در این صفحه." if orders else "رسید در انتظاری وجود نداره."),
        reply_markup=_kb(rows),
    )
    await callback.answer()


@fastly_router.callback_query(F.data.startswith("fastlyadmin:review:"))
async def review_order(callback:CallbackQuery):
    if not await _staff_guard(callback):return
    ident=int(callback.data.rsplit(":",1)[1])
    order=await fastly_store.order(ident)
    if not order:
        await callback.answer("سفارش وجود ندارد.",show_alert=True);return
    await callback.message.edit_text(
        f"🧾 <b>سفارش #{ident}</b>\n"
        f"مشتری: <code>{order['buyer_id']}</code>\n"
        f"محصول: {escape(order['title_snapshot'])}\n"
        f"تعداد: {order['quantity']}\nمبلغ: {order['total_toman']:,} تومان\n"
        f"وضعیت: {escape(order['state'])}\n\n"
        "تأیید فقط پس از تطبیق تراکنش واقعی بانکی.",
        reply_markup=_kb([
            [await _button("تأیید و تحویل",f"fastlyadmin:approve:{ident}","✅")],
            [await _button("رد رسید",f"fastlyadmin:reject:{ident}","❌")],
            [await _button("بازگشت","fastlyadmin:orders","↩️")],
        ]),
    )
    await callback.answer()


@fastly_router.callback_query(F.data.startswith("fastlyadmin:approve:"))
async def approve_order(callback:CallbackQuery):
    if not await _staff_guard(callback):return
    ident=int(callback.data.rsplit(":",1)[1])
    try:
        order=await fastly_store.order(ident)
        if not order:raise FastlyStoreError("سفارش پیدا نشد.")
        if not await trust_service.is_identity_approved(order["buyer_id"]):
            raise FastlyStoreError("احراز هویت مشتری تأیید نشده است.")
        result=await fastly_store.approve(ident,_uid(callback))
        if result=="out_of_stock":
            await callback.message.answer(
                f"⚠️ پرداخت سفارش #{ident} تأیید شد، ولی موجودی کافی نیست. "
                "برای تأمین موجودی یا بازپرداخت دستی اقدام کن."
            )
            try:
                await callback.bot.send_message(
                    order["buyer_id"],await _message("fastly_out_of_stock",order_id=ident)
                )
            except Exception:pass
        elif result=="allocated":
            try:
                await callback.bot.send_message(
                    order["buyer_id"],await _message("fastly_ready",order_id=ident),
                    reply_markup=_kb([
                        [await _button("دریافت اکانت‌ها",f"fastly:rules:{ident}","🔐")]
                    ]),
                )
            except Exception:
                logger.warning("Fastly ready notice failed for order %s",ident)
            await callback.message.answer(f"✅ موجودی سفارش #{ident} اختصاص داده شد.")
        else:
            await callback.message.answer("این سفارش قبلاً تخصیص داده شده است.")
    except Exception as exc:
        await callback.answer(_error(exc),show_alert=True);return
    await callback.answer("ثبت شد.")
    await _admin_home(callback.message)


@fastly_router.callback_query(F.data.startswith("fastlyadmin:reject:"))
async def reject_order(callback:CallbackQuery):
    if not await _staff_guard(callback):return
    ident=int(callback.data.rsplit(":",1)[1])
    order=await fastly_store.order(ident)
    if not order or not await fastly_store.reject(ident,_uid(callback)):
        await callback.answer("امکان رد این سفارش وجود ندارد.",show_alert=True);return
    try:
        await callback.bot.send_message(order["buyer_id"],await _message("fastly_rejected",order_id=ident))
    except Exception:pass
    await callback.answer("رسید رد شد.")
    await _admin_home(callback.message)


@fastly_router.callback_query(F.data == "fastlyadmin:claims")
async def admin_claims(callback:CallbackQuery):
    if not await _staff_guard(callback):return
    claims=await fastly_store.claims("pending")+await fastly_store.claims("awaiting_stock")
    rows=[
        [await _button(f"#{c['id']} · سفارش #{c['order_id']} · {c['reason_code']}",
                       f"fastlyadmin:claimview:{c['id']}","🛡")]
        for c in claims
    ]
    rows.append([await _button("بازگشت","fastlyadmin:home","↩️")])
    await callback.message.edit_text(
        "🛡 <b>درخواست‌های تعویض</b>\n\n"
        + (f"{len(claims)} مورد نیازمند بررسی." if claims else "درخواستی در انتظار نیست."),
        reply_markup=_kb(rows),
    )
    await callback.answer()


@fastly_router.callback_query(F.data.startswith("fastlyadmin:claimview:"))
async def admin_claim_details(callback:CallbackQuery):
    if not await _staff_guard(callback):return
    ident=int(callback.data.rsplit(":",1)[1])
    claim=await fastly_store.claim_record(ident)
    if not claim:
        await callback.answer("درخواست پیدا نشد.",show_alert=True);return
    await callback.message.edit_text(
        f"🛡 درخواست #{ident}\nمشتری: <code>{claim['buyer_id']}</code>\n"
        f"واحد: #{claim['order_item_id']}\nدلیل: {escape(CLAIM_REASONS.get(claim['reason_code'],'سایر'))}\n"
        f"توضیح: {escape(claim['reason_note'] or '—')}\n"
        f"وضعیت: {claim['state']}",
        reply_markup=_kb([
            [await _button("تأیید تعویض",f"fastlyadmin:claimyes:{ident}","✅")],
            [await _button("رد درخواست",f"fastlyadmin:claimno:{ident}","❌")],
            [await _button("بازگشت","fastlyadmin:claims","↩️")],
        ]),
    )
    await callback.answer()


@fastly_router.callback_query(F.data.startswith("fastlyadmin:claimyes:"))
@fastly_router.callback_query(F.data.startswith("fastlyadmin:claimno:"))
async def decide_claim(callback:CallbackQuery):
    if not await _staff_guard(callback):return
    ident=int(callback.data.rsplit(":",1)[1])
    approve=callback.data.startswith("fastlyadmin:claimyes:")
    claim=await fastly_store.claim_record(ident)
    if not claim:
        await callback.answer("درخواست پیدا نشد.",show_alert=True);return
    try:
        result=await fastly_store.decide_claim(ident,_uid(callback),approve)
        texts={
            "replaced": "✅ تعویض تأیید شد. اکانت جدید از سفارش‌های من قابل دریافت است.",
            "rejected": "درخواست تعویض پس از بررسی تأیید نشد.",
            "awaiting_stock": "درخواست تأیید شده اما برای تحویل جایگزین منتظر موجودی هستیم.",
        }
        if result in texts:
            try:
                await callback.bot.send_message(claim["buyer_id"],texts[result],reply_markup=_kb([
                    [await _button("سفارش‌های من","fastly:orders","🧾")]
                ]))
            except Exception:pass
        await callback.answer("نتیجه: "+result)
        await _admin_home(callback.message)
    except Exception as exc:
        await callback.answer(_error(exc),show_alert=True)


@fastly_router.callback_query(F.data == "fastlyadmin:stats")
async def admin_stats(callback:CallbackQuery):
    if not await _staff_guard(callback):return
    s=await fastly_store.stats()
    reasons="\n".join(
        f"• {escape(REASONS.get(x['reason_code'],x['reason_code']))}: {x['count']}"
        for x in s["reasons"]
    )
    await callback.message.edit_text(
        "📊 <b>آمار فروشگاه Fastly</b>\n\n"
        f"📦 آزاد: {s['available']} · تخصیص‌یافته: {s['delivered']}\n"
        f"🛒 سفارش‌ها: {s['orders']} · تکمیل‌شده: {s['paid_orders']}\n"
        f"🔢 واحد فروخته‌شده: {s['sold_units']}\n"
        f"💳 تأیید پرداخت / فروش ثبت‌شده: {s['revenue_toman']:,} تومان\n"
        f"🧾 رسیدهای در انتظار: {s['pending']}\n"
        f"🛡 تعویض انجام‌شده: {s['replacements']} · درخواست باز: {s['claims_pending']}\n\n"
        f"<b>دلایل خرید</b>\n{reasons or 'موردی ثبت نشده'}",
        reply_markup=_kb([[await _button("بازگشت","fastlyadmin:home","↩️")]]),
    )
    await callback.answer()
