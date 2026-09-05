from pathlib import Path

from handlers.trial_ui_v2 import TRIAL_UI_DEFAULTS
from handlers.ui_editor_v2 import _button_category, _message_category


def test_trial_success_card_hides_internal_service_and_config_username():
    body = TRIAL_UI_DEFAULTS["trial_v2_config_success"]
    assert "service" not in body.lower()
    assert "سرویس" not in body
    assert "{username}" not in body
    assert "{traffic}" in body
    assert "{minutes}" in body


def test_trial_templates_cover_all_customer_trial_screens():
    assert set(TRIAL_UI_DEFAULTS) == {
        "trial_v2_root",
        "trial_v2_config_select",
        "trial_v2_panel_select",
        "trial_v2_config_success",
        "trial_v2_panel_username",
        "trial_v2_panel_success",
    }
    assert all(str(body).strip() for body in TRIAL_UI_DEFAULTS.values())


def test_message_editor_groups_trial_copy_together():
    assert _message_category("trial_v2_root") == "trial"
    assert _message_category("trial_v2_config_success") == "trial"
    assert _message_category("public_payment_instructions") == "sales"
    assert _message_category("backup_created") == "backup"


def test_button_editor_groups_customer_flows():
    assert _button_category("trialv2:choose:config") == "trial"
    assert _button_category("svcmarket:s:a:1") == "sales"
    assert _button_category("public_order_14") == "payment"
    assert _button_category("back_to_main") == "main"


def test_router_order_places_v2_before_old_flat_handlers():
    source = Path("handlers/__init__.py").read_text(encoding="utf-8")
    assert source.index("from .trial_ui_v2 import") < source.index("from .operations_bootstrap import")
    assert source.index("include_router(trial_ui_v2_router)") < source.index("include_router(service_marketplace_router)")
    assert source.index("include_router(ui_editor_v2_router)") < source.index("include_router(premium_ui_clean_buttons_router)")
