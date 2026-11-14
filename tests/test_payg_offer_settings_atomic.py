"""An enabled PAYG offer cannot be left with an invalid rate."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import payg_offers_service as offers_module
from payg_service import PaygService


@pytest.mark.asyncio
async def test_enabled_offer_rejects_zero_rate_without_persisting(tmp_path, monkeypatch):
    path = str(tmp_path / "offers.db")
    monkeypatch.setattr(offers_module, "payg_service", PaygService(path))
    monkeypatch.setattr(
        offers_module, "get_service_by_rebecca_id",
        AsyncMock(return_value=SimpleNamespace(display_name="test")),
    )
    service = offers_module.PaygOfferService(path)
    await service.ensure_offer(7)
    await service.update_offer(7, price_per_gb_toman=2500, enabled=True)
    with pytest.raises(offers_module.PaygOfferError):
        await service.update_offer(7, price_per_gb_toman=0)
    current = await service.get_offer(7)
    assert current["enabled"] == 1
    assert current["price_per_gb_toman"] == 2500
