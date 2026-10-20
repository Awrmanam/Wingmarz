import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from handlers import public_handlers
from handlers import admin_handlers
from handlers import payg


def run(coro):
    return asyncio.run(coro)


class FakeState:
    def __init__(self, data):
        self.data = dict(data)
        self.clear = AsyncMock()

    async def get_data(self):
        return dict(self.data)


def _message():
    return SimpleNamespace(
        from_user=SimpleNamespace(id=55, username="buyer"),
        photo=[],
        answer=AsyncMock(),
    )


def test_public_receipt_without_photo_returns_prompt_not_index_error():
    message = _message()
    state = FakeState({"order_id": 10})
    run(public_handlers.public_receive_payment_receipt(message, state))
    message.answer.assert_awaited_once()


def test_admin_receipt_without_photo_returns_prompt_not_index_error():
    message = _message()
    state = FakeState({"order_id": 11})
    run(admin_handlers.admin_receive_payment_receipt(message, state))
    message.answer.assert_awaited_once()


def test_payg_receipt_without_photo_returns_prompt_not_index_error():
    message = _message()
    state = FakeState({"payg_topup_id": 12})
    run(payg.payg_receipt_received(message, state))
    message.answer.assert_awaited_once_with("لطفاً رسید را به‌صورت عکس ارسال کنید.")
