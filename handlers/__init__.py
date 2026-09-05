"""Handlers package composition."""

# The bot registers style_admin_router before legacy sudo/admin/public routers.
# Operational children intentionally upgrade a small set of callbacks while
# preserving the legacy handlers as fallbacks.
from .style_admin import style_admin_router
# Import trial_ui_v2 before operations_bootstrap so its editable message
# defaults are present when PremiumUIService snapshots config.MESSAGES.
from .trial_ui_v2 import trial_ui_v2_router
from .operations_bootstrap import operations_bootstrap_router
from .service_marketplace import service_marketplace_router
from .service_marketplace_settings import service_marketplace_settings_router
from .trial_experience import trial_experience_router
from .premium_ui_clean_buttons import premium_ui_clean_buttons_router
from .premium_ui_admin import premium_ui_admin_router
from .ui_editor_v2 import ui_editor_v2_router
from .operations import operations_router
from . import dashboard_label_fix as _dashboard_label_fix  # presentation-only menu label fallback
from . import operations_runtime as _operations_runtime  # checkout adapter registration
import premium_template_runtime as _premium_template_runtime  # preserve {emoji:key} through .format()
import premium_bot_runtime as _premium_bot_runtime  # catalog/style all outgoing inline keyboards
import service_marketplace_runtime as _service_marketplace_runtime  # add free-trial entry to user homes
from .operations_public import operations_public_router
from .control_center import control_center_router
from .control_center_start import control_center_start_router

style_admin_router.include_router(operations_bootstrap_router)
# Cleaner user-facing trial routes must win before the older service-market trial
# handlers. Paid purchase remains in service_marketplace_router unchanged.
style_admin_router.include_router(trial_ui_v2_router)
# Service-first sale/trial routes must win before the older plan-first handlers.
style_admin_router.include_router(service_marketplace_router)
style_admin_router.include_router(service_marketplace_settings_router)
style_admin_router.include_router(trial_experience_router)
# Categorized UI editor wins before the old flat catalogs.
style_admin_router.include_router(ui_editor_v2_router)
# Clean button catalog remains available for shared loading/detail behavior.
style_admin_router.include_router(premium_ui_clean_buttons_router)
# Detailed text/button editors remain as fallback handlers.
style_admin_router.include_router(premium_ui_admin_router)
style_admin_router.include_router(operations_router)
style_admin_router.include_router(operations_public_router)
style_admin_router.include_router(control_center_start_router)
style_admin_router.include_router(control_center_router)
