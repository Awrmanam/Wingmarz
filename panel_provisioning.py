from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from panel_providers import get_panel_provider
from rebecca_api import rebecca_api


class ProviderProvisioningError(RuntimeError):
    """Base error for provider-specific panel provisioning."""


class ProviderNotImplemented(ProviderProvisioningError):
    """Raised when a visible provider does not have an adapter yet."""


@dataclass(frozen=True, slots=True)
class PanelProvisionRequest:
    user_id: int
    username: str
    password: str
    data_limit: int | None
    expire: int | None
    users_limit: int | None
    services: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class PanelProvisionResult:
    provider: str
    username: str
    password: str
    remote: dict[str, Any]


async def provision_panel(provider_key: str, request: PanelProvisionRequest) -> PanelProvisionResult:
    """Provision one reseller/admin panel through the selected provider.

    Rebecca is wired to the existing verified provisioning path. Sanaei is
    intentionally a typed placeholder so its API can be added without changing
    the purchase UI contract.
    """
    provider = get_panel_provider(provider_key)
    if provider is None:
        raise ProviderProvisioningError("Unknown panel provider")
    if not provider.implemented:
        raise ProviderNotImplemented(f"{provider.key} provisioning is not implemented yet")

    if provider.key == "rebecca":
        remote = await rebecca_api.create_admin_verified(
            request.username,
            request.password,
            int(request.user_id),
            data_limit=request.data_limit,
            expire=request.expire,
            users_limit=request.users_limit,
            services=[int(item) for item in request.services],
        )
        return PanelProvisionResult(
            provider=provider.key,
            username=request.username,
            password=request.password,
            remote=remote,
        )

    raise ProviderNotImplemented(f"{provider.key} provisioning is not implemented yet")
