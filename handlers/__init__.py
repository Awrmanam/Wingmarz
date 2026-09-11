"""Handlers package composition."""

from .style_admin import style_admin_router
from .trial_ui_v2 import trial_ui_v2_router
from .panel_trial_restore import panel_trial_restore_router
from .operations_bootstrap import operations_bootstrap_router
from .product_center import product_center_router
from .plan_storefront import plan_storefront_router
from .panel_experience import panel_experience_router
from .service_marketplace import service_marketplace_router
from .service_marketplace_settings import service_marketplace_settings_router
from .trial_experience import trial_experience_router
from .premium_ui_clean_buttons import premium_ui_clean_buttons_router
from .premium_ui_admin import premium_ui_admin_router
from .button_editor_context import button_editor_context_router
from .ui_editor_v2 import ui_editor_v2_router
from .operations import operations_router
from .home_navigation import home_navigation_router
from .operations_public import operations_public_router
from .support_v2 import support_v2_router
from .tariffs import tariffs_router
from .control_center import control_center_router

style_admin_router.include_router(operations_bootstrap_router)
# Active panel-trial restoration must run before the issuance/cooldown router so
# old Telegram messages cannot accidentally create or request a second trial.
style_admin_router.include_router(panel_trial_restore_router)
# trial_ui_v2 is the single canonical owner of config/panel trial issuance.
style_admin_router.include_router(trial_ui_v2_router)
style_admin_router.include_router(product_center_router)
style_admin_router.include_router(plan_storefront_router)
# Renewal/extension also resolves panel identity through the same catalog.
style_admin_router.include_router(panel_experience_router)
# Provider-specific routes remain internal for discovery/provisioning/settings.
style_admin_router.include_router(service_marketplace_router)
style_admin_router.include_router(service_marketplace_settings_router)
style_admin_router.include_router(trial_experience_router)
# Contextual button editor must precede the generic categorized editor.
style_admin_router.include_router(button_editor_context_router)
style_admin_router.include_router(ui_editor_v2_router)
style_admin_router.include_router(premium_ui_clean_buttons_router)
style_admin_router.include_router(premium_ui_admin_router)
style_admin_router.include_router(operations_router)
style_admin_router.include_router(home_navigation_router)
style_admin_router.include_router(operations_public_router)
# User-facing support and tariffs intercept their legacy callbacks before the
# older control-center fallback handlers.
style_admin_router.include_router(support_v2_router)
style_admin_router.include_router(tariffs_router)
style_admin_router.include_router(control_center_router)
