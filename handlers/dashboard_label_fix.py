"""Fix operational dashboard labels when no visual alias is configured.

The Style Engine returns the raw business identity when an alias does not exist.
For menu buttons that raw identity is the callback_data, which must never leak
into the user-visible button label. Keep callback_data unchanged and fall back
to the Persian default label instead.
"""

from style_engine import style_engine
from . import operations as _operations


async def resolve_menu_label(callback_data: str, default: str) -> str:
    try:
        raw, visible = await style_engine.resolve_visual_alias("menu", callback_data)
    except Exception:
        return default
    return default if visible == raw else visible


# operations.build_operational_dashboard_keyboard resolves this global at runtime,
# so replacing only the presentation helper fixes /start and back_to_main without
# changing any callback/business identity or checkout behavior.
_operations._menu_label = resolve_menu_label
