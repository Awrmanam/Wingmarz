"""Handlers package composition."""

# The bot registers style_admin_router before legacy sudo/admin/public routers.
# Operations must be the first child because it intentionally upgrades a small
# set of existing callbacks (dashboard, discounts, trials and checkout) while
# preserving the legacy handlers as fallbacks.
from .style_admin import style_admin_router
from .operations import operations_router
from .control_center import control_center_router
from .control_center_start import control_center_start_router

style_admin_router.include_router(operations_router)
style_admin_router.include_router(control_center_start_router)
style_admin_router.include_router(control_center_router)
