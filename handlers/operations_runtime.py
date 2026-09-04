from __future__ import annotations

from aiogram.types import Message

from . import operations as operations_module


class _MessageProxy:
    def __init__(self, message: Message):
        self._message = message

    async def edit_text(self, text: str, **kwargs):
        return await self._message.answer(text, **kwargs)

    async def answer(self, text: str, **kwargs):
        return await self._message.answer(text, **kwargs)


class SafeMessageCheckoutAdapter:
    """Adapt an incoming user Message to the checkout renderer safely.

    Telegram bots cannot edit a user's incoming message, so edit_text is mapped
    to sending a new bot message instead.
    """

    def __init__(self, message: Message):
        self.message = _MessageProxy(message)
        self.from_user = message.from_user
        self.bot = message.bot

    async def answer(self, *_args, **_kwargs):
        return None


operations_module._MessageCheckoutAdapter = SafeMessageCheckoutAdapter
