"""Handlers package composition."""

# The bot already registers style_admin_router before the legacy sudo router.
# Attach the control-center router as a child so dashboard/ticket callbacks are
# handled before legacy sudo callbacks without changing business handlers.
from .style_admin import style_admin_router
from .control_center import control_center_router

style_admin_router.include_router(control_center_router)
