from __future__ import annotations

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True, slots=True)
class PanelProvider:
    """Provider metadata used by the customer purchase flow.

    `implemented` means Wingmarz already has a provisioning adapter for the
    provider. A provider can stay visible while its adapter is still pending so
    the UI and product model do not need to be redesigned later.
    """

    key: str
    label: str
    implemented: bool
    description: str


_PROVIDERS: Final[dict[str, PanelProvider]] = {
    "rebecca": PanelProvider(
        key="rebecca",
        label="پنل Rebecca",
        implemented=True,
        description="صدور خودکار ادمین و تحویل اطلاعات ورود",
    ),
    "sanaei": PanelProvider(
        key="sanaei",
        label="پنل Sanaei",
        implemented=False,
        description="اسکلت آماده است؛ اتصال API در فاز بعد اضافه می‌شود",
    ),
}


def list_panel_providers() -> tuple[PanelProvider, ...]:
    """Return providers in the stable order shown to customers."""
    return tuple(_PROVIDERS.values())


def get_panel_provider(key: str) -> PanelProvider | None:
    return _PROVIDERS.get(str(key or "").strip().lower())
