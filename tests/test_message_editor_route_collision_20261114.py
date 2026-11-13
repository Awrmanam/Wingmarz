"""Regression: old button routes must not hijack the active message editor."""
from pathlib import Path
from types import SimpleNamespace

from handlers.button_editor_context import (
    button_editor_context_router,
    legacy_editor_redirect,
)
from handlers.ui_editor_v2 import (
    ui_editor_v2_router,
    message_category,
    message_save_draft,
    message_detail,
    message_search_start,
)


def _matching_handlers(router, callback_data):
    event = SimpleNamespace(data=callback_data)
    result = []
    for handler in router.callback_query.handlers:
        if all(
            bool(getattr(filter_obj.callback, "resolve")(event))
            for filter_obj in handler.filters
            if hasattr(filter_obj.callback, "resolve")
        ):
            # No handler-specific non-magic filters in the routes checked here.
            if all(hasattr(f.callback, "resolve") for f in handler.filters):
                result.append(handler.callback)
    return result


def test_live_text_editor_callbacks_are_not_redirected_to_button_editor():
    text_callbacks = [
        "uiv2:mc:start",
        "uiv2:mc:payment:1",
        "uiv2:m:home_reseller_title",
        "uiv2:me:home_public_body",
        "uiv2:mr:home_reseller_footer",
        "uiv2:mp:home_reseller_title",
        "uiv2:mt:home_public_title",
        "uiv2:search",
        "uiv2:save",
    ]
    for data in text_callbacks:
        assert legacy_editor_redirect not in _matching_handlers(button_editor_context_router, data), data


def test_message_editor_handlers_remain_reachable():
    assert message_category in _matching_handlers(ui_editor_v2_router, "uiv2:mc:start")
    assert message_detail in _matching_handlers(ui_editor_v2_router, "uiv2:m:home_reseller_title")
    assert message_search_start in _matching_handlers(ui_editor_v2_router, "uiv2:search")
    assert message_save_draft in _matching_handlers(ui_editor_v2_router, "uiv2:save")


def test_old_button_editor_callbacks_still_redirect():
    for data in ("uiv2:bc:main:0", "uiv3:scope:customer"):
        assert legacy_editor_redirect in _matching_handlers(button_editor_context_router, data)


def test_redirect_scope_never_uses_broad_uiv2_prefix():
    source = Path("handlers/button_editor_context.py").read_text(encoding="utf-8")
    assert '@button_editor_context_router.callback_query(F.data.startswith("uiv2:"))' not in source
    assert '@button_editor_context_router.callback_query(F.data.startswith("uiv2:bc:"))' in source
