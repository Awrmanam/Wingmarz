"""Presentation metadata for the PAYG feature.

Kept separate from the core registry so the billing feature stays modular while
still participating in the existing button editor and repository-wide UI audit.
"""
from __future__ import annotations

import ui_presentation_registry as registry
from ui_presentation_registry import ButtonDef


_DEFS = (
    ButtonDef("admin.finance.payg.home", "admin.finance", "مدیریت PAYG و کیف پول", "paygadmin:home", rank=20),
    ButtonDef("admin.finance.payg.service_home", "admin.finance", "تعرفه‌های PAYG بر اساس سرویس", "paygsvcadmin:home", rank=20),
    ButtonDef("admin.finance.payg.toggle", "admin.finance", "فعال یا غیرفعال کردن PAYG", "paygadmin:toggle", rank=21),
    ButtonDef("admin.finance.payg.rate", "admin.finance", "نرخ هر گیگ PAYG", "paygadmin:set:rate", rank=22),
    ButtonDef("admin.finance.payg.minimum", "admin.finance", "حداقل شارژ PAYG", "paygadmin:set:minimum", rank=23),
    ButtonDef("admin.finance.payg.users", "admin.finance", "حد کاربر PAYG", "paygadmin:set:users", rank=24),
    ButtonDef("admin.finance.payg.services", "admin.finance", "سرویس‌های PAYG", "paygadmin:set:services", rank=25),
    ButtonDef("purchase.payg.back", "purchase.entry", "بازگشت به PAYG", "payg:offer:a:rebecca", rank=80, navigation_type="back"),
)


for definition in _DEFS:
    if any(item.key == definition.key for item in registry.BUTTONS):
        continue
    registry.BUTTONS = (*registry.BUTTONS, definition)
    registry._BY_CALLBACK.setdefault(definition.callback_data, []).append(definition)
