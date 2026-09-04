from __future__ import annotations

from premium_markup_runtime import style_reply_markup
from utils.bold_fix_bot import BoldFixBot


_original_call = BoldFixBot.__call__


async def premium_ui_call(self, method, *args, **kwargs):
    try:
        markup = getattr(method, "reply_markup", None)
        if markup is not None:
            setattr(method, "reply_markup", await style_reply_markup(markup))
    except Exception:
        # Presentation customization must never block a business operation.
        pass
    return await _original_call(self, method, *args, **kwargs)


if getattr(BoldFixBot.__call__, "__name__", "") != "premium_ui_call":
    BoldFixBot.__call__ = premium_ui_call
