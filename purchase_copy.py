"""Shared presentation-only renderer for editable purchase wizard messages.

The editor owns config.MESSAGES and validates allowed {field} variables.
The purchase flow supplies only HTML-escaped dynamic fields. We deliberately
keep all callback IDs, billing choices, prices and entitlement logic elsewhere.
"""
from __future__ import annotations

import config
from premium_ui_service import premium_ui_service
from text_templates import PremiumTemplateString


async def purchase_copy(key: str, **html_fields: str) -> str:
    """Render one validated purchase template with Premium Emoji placeholders.

    Dynamic field values are HTML fragments escaped by the caller. They must
    never include untrusted raw markup.
    """
    template = PremiumTemplateString(str(config.MESSAGES[key]))
    formatted = template.format(**html_fields)
    return await premium_ui_service.render_placeholders(str(formatted))
