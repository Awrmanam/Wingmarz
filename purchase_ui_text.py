"""Editable customer-facing purchase wizard message rendering.

Business callbacks, prices, availability, and checkout state live in their
respective handlers. This module only formats validated presentation strings.
"""
from __future__ import annotations

import config
from premium_ui_service import premium_ui_service
from text_templates import PremiumTemplateString


async def purchase_message(key: str, **values: str) -> str:
    """Format a registered UI template, preserving custom emoji tokens.

    Dynamic values must already be escaped as Telegram HTML by callers.
    Format before resolving custom emojis so {emoji:key} isn't treated as
    Python's regular string interpolation syntax.
    """
    template = PremiumTemplateString(str(config.MESSAGES[key]))
    formatted = template.format(**values) if values else template
    return await premium_ui_service.render_placeholders(str(formatted))
