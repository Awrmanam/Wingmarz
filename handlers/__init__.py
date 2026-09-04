"""Handlers package composition."""

# The bot registers style_admin_router before legacy sudo/admin/public routers.
# Operational children intentionally upgrade a small set of callbacks while
# preserving the legacy handlers as fallbacks.
from .style_admin import style_admin_router
from .operations_bootstrap import operations_bootstrap_router
from .trial_experience import trial_experience_router
from .premium_ui_clean_buttons import premium_ui_clean_buttons_router
from .premium_ui_admin import premium_ui_admin_router
from .operations import operations_router
from . import dashboard_label_fix as _dashboard_label_fix  # presentation-only menu label fallback
from . import operations_runtime as _operations_runtime  # checkout adapter registration
import premium_template_runtime as _premium_template_runtime  # preserve {emoji:key} through .format()
import premium_bot_runtime as _premium_bot_runtime  # catalog/style all outgoing inline keyboards
from .operations_public import operations_public_router
from .control_center import control_center_router
from .control_center_start import control_center_start_router

style_admin_router.include_router(operations_bootstrap_router)
style_admin_router.include_router(trial_experience_router)
# Clean button catalog intercepts cc:buttons before the detailed editor handlers.
style_admin_router.include_router(premium_ui_clean_buttons_router)
# Intercept cc:texts and button detail actions with the richer editors before operations.py.
style_admin_router.include_router(premium_ui_admin_router)
style_admin_router.include_router(operations_router)
style_admin_router.include_router(operations_public_router)
style_admin_router.include_router(control_center_start_router)
style_admin_router.include_router(control_center_router)
