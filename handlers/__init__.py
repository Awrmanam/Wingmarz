"""Handlers package composition."""

# The bot registers style_admin_router before legacy sudo/admin/public routers.
# Operational children intentionally upgrade a small set of callbacks while
# preserving the legacy handlers as fallbacks.
from .style_admin import style_admin_router
# All template defaults are registered by config before routers are imported.
from .trial_ui_v2 import trial_ui_v2_router
from .operations_bootstrap import operations_bootstrap_router
from .service_marketplace import service_marketplace_router
from .service_marketplace_settings import service_marketplace_settings_router
from .trial_experience import trial_experience_router
from .premium_ui_clean_buttons import premium_ui_clean_buttons_router
from .premium_ui_admin import premium_ui_admin_router
from .ui_editor_v2 import ui_editor_v2_router
from .operations import operations_router
from .home_navigation import home_navigation_router
from .operations_public import operations_public_router
from .control_center import control_center_router

style_admin_router.include_router(operations_bootstrap_router)
# One trial presentation router owns current and historical service callbacks.
style_admin_router.include_router(trial_ui_v2_router)
# Service-first sale/trial routes must win before the older plan-first handlers.
style_admin_router.include_router(service_marketplace_router)
style_admin_router.include_router(service_marketplace_settings_router)
style_admin_router.include_router(trial_experience_router)
# Categorized editor owns text/button entry points.
style_admin_router.include_router(ui_editor_v2_router)
# Clean button catalog remains available for shared loading/detail behavior.
style_admin_router.include_router(premium_ui_clean_buttons_router)
# Detailed text/button editors remain as fallback handlers.
style_admin_router.include_router(premium_ui_admin_router)
# SUDO dashboard owns SUDO /start first. Role-aware navigation then owns regular
# admin /start plus every home/back alias; public /start remains the final path.
style_admin_router.include_router(operations_router)
style_admin_router.include_router(home_navigation_router)
style_admin_router.include_router(operations_public_router)
style_admin_router.include_router(control_center_router)
