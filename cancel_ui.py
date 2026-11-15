"""Add one premium-style Cancel affordance to private, interactive Telegram UIs.

No business callback or finance state is changed. Group approvals, completed
notifications, inline-mode edits and the six-action home remain untouched.
"""
from __future__ import annotations

import re
from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from style_engine import style_engine

CANCEL_CALLBACK = "ui:cancel"
_CANCEL_WORD = re.compile(r"(?:لغو|انصراف|cancel)",re.IGNORECASE)
_INPUT_PROMPT = re.compile(
    r"(?:وارد (?:کنید|نمایید)|ارسال (?:کنید|نمایید)|انتخاب کنید|"
    r"تایپ کنید|بنویسید|عدد را|مبلغ را|نام را|کد را|لطفاً.{0,110}(?:وارد|ارسال|بنویسید))"
)
_TERMINAL = re.compile(
    r"(?:✅.{0,45}(?:پرداخت (?:تأیید|ثبت)|خرید (?:موفق|تأیید)|"
    r"تراکنش (?:انجام|ثبت)|سفارش (?:ثبت|تأیید)|"
    r"شارژ تأیید|خدمت ساخته|عملیات تکمیل))"
)
_ROOT_CALLBACKS = frozenset({
    "customer:reports", "customer:explore", "customer:account",
})


def _private_method(method: Any) -> bool:
    chat_id=getattr(method,"chat_id",None)
    if isinstance(chat_id,bool):
        return False
    try:
        return int(chat_id)>0
    except (TypeError,ValueError):
        return False


async def _cancel_button(*, callback: str = CANCEL_CALLBACK) -> InlineKeyboardButton:
    try:
        return await style_engine.styled_button(
            "لغو",icon_key="cancel",fallback="❌",callback_data=callback
        )
    except Exception:
        return InlineKeyboardButton(text="❌ لغو",callback_data=callback)


def _is_home(markup: InlineKeyboardMarkup) -> bool:
    actions={str(button.callback_data or "") for row in markup.inline_keyboard
             for button in row}
    return _ROOT_CALLBACKS.issubset(actions)


async def decorate_cancel(method: Any) -> None:
    """Mutate the outgoing aiogram method before BoldFixBot sends it.

    - Inline menus always have a single cancel affordance (except root).
    - Native ReplyKeyboardMarkup gets a plain Unicode fallback (premium button
      emoji is not available on Telegram reply keyboards).
    - Plain text prompts get inline cancel without changing informational posts.
    """
    if not _private_method(method):
        return
    markup=getattr(method,"reply_markup",None)
    if isinstance(markup,InlineKeyboardMarkup):
        if _is_home(markup):
            return
        rows=[]
        found=False
        for row in markup.inline_keyboard:
            new_row=[]
            for button in row:
                cb=str(button.callback_data or "")
                label=str(button.text or "")
                if cb==CANCEL_CALLBACK:
                    # Outgoing API messages can pass through formatting more
                    # than once. Already-canonical buttons must be idempotent.
                    new_row.append(button)
                    found=True
                    continue
                if _CANCEL_WORD.search(label):
                    # Preserve intentional business actions while using the
                    # configured premium 'cancel' key for visual consistency.
                    if cb and (cb==CANCEL_CALLBACK or
                               cb in {"style:cancel","kyc:cancel","support:cancel"}):
                        cb=CANCEL_CALLBACK
                    if cb and not found:
                        new_row.append(await _cancel_button(callback=cb))
                        found=True
                    elif cb:
                        new_row.append(button)
                    else:
                        new_row.append(button)
                else:
                    new_row.append(button)
            rows.append(new_row)
        if not found:
            rows.append([await _cancel_button()])
        method.reply_markup=markup.model_copy(update={"inline_keyboard":rows})
        return
    if isinstance(markup,ReplyKeyboardMarkup):
        if any(_CANCEL_WORD.search(str(item.text)) for row in markup.keyboard for item in row):
            return
        rows=[list(row) for row in markup.keyboard]
        rows.append([KeyboardButton(text="❌ لغو")])
        method.reply_markup=markup.model_copy(update={"keyboard":rows})
        return
    if markup is not None:
        return

    method_name=type(method).__name__
    if method_name not in {"SendMessage","EditMessageText","EditMessageCaption",
                           "SendPhoto","SendDocument","SendVideo"}:
        return
    text=getattr(method,"text",None) or getattr(method,"caption",None) or ""
    if not isinstance(text,str) or _TERMINAL.search(text) or not _INPUT_PROMPT.search(text):
        return
    method.reply_markup=InlineKeyboardMarkup(inline_keyboard=[[await _cancel_button()]])
