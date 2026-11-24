"""Regression: clicking the same FIXED/PAYG view twice must be harmless.

Only the exact Telegram 'message is not modified' edit error is ignored.
Unrelated API errors remain visible to monitoring.
"""
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import EditMessageText, EditMessageReplyMarkup, SendMessage
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from utils.bold_fix_bot import BoldFixBot, _is_unchanged_message_edit


def _bot():
    return BoldFixBot(token="123456:ABCDEFabcdefghijklmnopqrstuvwxyz12345678")


def _same_error(method):
    return TelegramBadRequest(
        method=method,
        message="Telegram server says - Bad Request: message is not modified: "
                "specified new message content and reply markup are exactly "
                "the same as a current content and reply markup of the message",
    )


@pytest.mark.asyncio
async def test_duplicate_fixed_dashboard_edit_is_safely_ignored(monkeypatch):
    method = EditMessageText(
        chat_id=42, message_id=18,
        text="📦 <b>سرویس حجمی/زمانی — ProMax</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="بازگشت", callback_data="customer:fixed:8")
        ]]),
    )
    api = AsyncMock(side_effect=_same_error(method))
    monkeypatch.setattr(Bot, "__call__", api)
    bot = _bot()
    try:
        assert await bot(method) is True
        api.assert_awaited_once()
    finally:
        await bot.session.close()


@pytest.mark.asyncio
async def test_identical_reply_markup_edit_is_a_noop(monkeypatch):
    method = EditMessageReplyMarkup(chat_id=42, message_id=18)
    monkeypatch.setattr(Bot, "__call__", AsyncMock(side_effect=_same_error(method)))
    bot = _bot()
    try:
        assert await bot(method) is True
    finally:
        await bot.session.close()


@pytest.mark.asyncio
async def test_unrelated_telegram_edit_errors_are_not_suppressed(monkeypatch):
    method = EditMessageText(chat_id=42, message_id=18, text="Different content")
    error = TelegramBadRequest(
        method=method,
        message="Bad Request: message to edit not found",
    )
    monkeypatch.setattr(Bot, "__call__", AsyncMock(side_effect=error))
    bot = _bot()
    try:
        with pytest.raises(TelegramBadRequest, match="message to edit not found"):
            await bot(method)
    finally:
        await bot.session.close()


@pytest.mark.asyncio
async def test_same_error_on_non_edit_method_is_never_suppressed(monkeypatch):
    method = SendMessage(chat_id=42, text="Welcome")
    error = _same_error(method)
    monkeypatch.setattr(Bot, "__call__", AsyncMock(side_effect=error))
    bot = _bot()
    try:
        with pytest.raises(TelegramBadRequest, match="message is not modified"):
            await bot(method)
    finally:
        await bot.session.close()


def test_only_edit_methods_and_exact_noop_error_match():
    edit = EditMessageText(chat_id=42, message_id=8, text="X")
    send = SendMessage(chat_id=42, text="X")
    assert _is_unchanged_message_edit(edit, _same_error(edit))
    assert not _is_unchanged_message_edit(send, _same_error(send))
    assert not _is_unchanged_message_edit(edit, TelegramBadRequest(
        method=edit, message="Bad Request: can't parse entities"
    ))
