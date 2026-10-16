"""Presentation metadata for KYC controls.

KYC is modular but its visible static buttons still participate in the central
button editor/audit registry.
"""
from __future__ import annotations

import ui_presentation_registry as registry
from ui_presentation_registry import ButtonDef


_DEFS = (
    ButtonDef("reseller.kyc.status", "reseller.account", "وضعیت احراز هویت", "kyc:status", rank=30),
    ButtonDef("reseller.kyc.start", "reseller.account", "شروع یا ارسال مجدد احراز هویت", "kyc:start", rank=31),
    ButtonDef("reseller.kyc.cancel", "reseller.account", "لغو احراز هویت", "kyc:cancel", rank=32, navigation_type="back"),
    ButtonDef("admin.access.kyc.pending", "admin.access", "KYCهای در انتظار", "kycadmin:pending", rank=20),
)


for definition in _DEFS:
    if any(item.key == definition.key for item in registry.BUTTONS):
        continue
    registry.BUTTONS = (*registry.BUTTONS, definition)
    registry._BY_CALLBACK.setdefault(definition.callback_data, []).append(definition)
