"""Dashboard integration: saved logical order never edits callback identities."""
import asyncio
from unittest.mock import AsyncMock

from aiogram.types import InlineKeyboardButton

from handlers import operations


def test_rendered_dashboard_uses_configured_order_and_visibility(monkeypatch):
    defaults = [row[0] for row in operations.MENU_SPECS]
    expected = list(reversed(defaults))
    monkeypatch.setattr(
        operations.operations_service, "get_menu_order",
        AsyncMock(return_value=expected),
    )
    monkeypatch.setattr(
        operations.operations_service, "menu_is_visible",
        AsyncMock(side_effect=lambda key:key!="ops:hub:finance"),
    )

    async def button(text, callback_data, **kwargs):
        return InlineKeyboardButton(text=text,callback_data=callback_data)
    monkeypatch.setattr(operations,"_button",button)

    markup=asyncio.run(operations.build_operational_dashboard_keyboard())
    flattened=[
        button.callback_data
        for row in markup.inline_keyboard for button in row
    ]
    assert flattened[0]==operations.QUICK_ACTIONS[0][0]
    assert flattened[1:]==[
        key for key in expected if key!="ops:hub:finance"
    ]
    assert "ops:hub:settings" in flattened
    assert flattened.count("ops:hub:panels")==1
