import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

import panel_provisioning
from panel_providers import get_panel_provider, list_panel_providers
from panel_provisioning import (
    PanelProvisionRequest,
    ProviderNotImplemented,
    provision_panel,
)


def test_provider_registry_exposes_rebecca_and_sanaei_in_customer_order():
    providers = list_panel_providers()
    assert [item.key for item in providers] == ["rebecca", "sanaei"]
    assert get_panel_provider("rebecca").implemented is True
    assert get_panel_provider("sanaei").implemented is False


def test_home_purchase_buttons_enter_provider_selection():
    source = Path("handlers/home_keyboards.py").read_text(encoding="utf-8")
    assert '"panelprov:root:p"' in source
    assert '"panelprov:root:a"' in source


def test_sanaei_provisioning_is_a_safe_placeholder():
    request = PanelProvisionRequest(
        user_id=42,
        username="panel42",
        password="secret",
        data_limit=None,
        expire=None,
        users_limit=None,
    )
    with pytest.raises(ProviderNotImplemented):
        asyncio.run(provision_panel("sanaei", request))


def test_rebecca_provisioning_reuses_verified_existing_api(monkeypatch):
    create = AsyncMock(return_value={"username": "panel42", "status": "active"})
    monkeypatch.setattr(panel_provisioning.rebecca_api, "create_admin_verified", create)
    request = PanelProvisionRequest(
        user_id=42,
        username="panel42",
        password="secret",
        data_limit=100,
        expire=123456,
        users_limit=10,
        services=(7, 9),
    )

    result = asyncio.run(provision_panel("rebecca", request))

    assert result.provider == "rebecca"
    assert result.username == "panel42"
    assert result.password == "secret"
    assert result.remote["status"] == "active"
    create.assert_awaited_once_with(
        "panel42",
        "secret",
        42,
        data_limit=100,
        expire=123456,
        users_limit=10,
        services=[7, 9],
    )
