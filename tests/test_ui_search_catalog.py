"""Pure editor catalog search never treats technical identities as human text."""
from types import SimpleNamespace

from handlers.button_editor_context import search_registered_buttons
from handlers.ui_editor_v2 import search_message_catalog
from premium_ui_service import MessageTemplateItem
from ui_presentation_registry import ButtonDef, ScreenDef


def test_searches_message_titles_and_body_but_not_internal_key():
    items = [
        MessageTemplateItem(
            key="invoicing_private_42",
            default_body="صورت حساب شما ثبت شد",
            body="پرداخت با موفقیت انجام شد",
            is_overridden=True,
        ),
        MessageTemplateItem(
            key="welcome_sudo",
            default_body="سلام مدیر",
            body="خوش آمدید",
            is_overridden=False,
        ),
    ]
    assert [
        row.key for row in search_message_catalog(items,"پرداخت")
    ] == ["invoicing_private_42"]
    assert not search_message_catalog(items,"invoicing_private_42")
    assert [row.key for row in search_message_catalog(items,"خوش")] == ["welcome_sudo"]


def test_searches_visible_button_labels_not_callback_or_runtime_ids():
    screen = ScreenDef(
        key="admin.finance",scope="manage",category="finance",title="پرداخت",rank=0,
    )
    logical = ButtonDef(
        key="admin.finance.one",screen="admin.finance",title="تایید مالی",
        callback_data="finance:secret:owner:123456",default_text="پرداخت",
        rank=1,
    )
    item = SimpleNamespace(
        default_text="پرداخت",display_text="بررسی رسید",id=99999,
    )
    rows = [(logical,screen,item)]
    assert search_registered_buttons(rows,"رسید") == rows
    assert search_registered_buttons(rows,"پرداخت") == rows
    assert not search_registered_buttons(rows,"finance:secret")
    assert not search_registered_buttons(rows,"99999")
