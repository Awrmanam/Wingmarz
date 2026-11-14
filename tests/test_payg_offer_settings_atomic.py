"""An enabled PAYG offer cannot be left with an invalid rate."""
import pytest

from payg_offers_service import PaygOfferError, PaygOfferService


@pytest.mark.asyncio
async def test_enabled_offer_rejects_zero_rate_without_persisting(tmp_path, monkeypatch):
    service = PaygOfferService(str(tmp_path / "offers.db"))
    await service.ensure_schema()
    # Avoid a remote catalog dependency: the offer is a local configuration row.
    await service.ensure_offer(7)
    await service.update_offer(7, price_per_gb_toman=2500, enabled=True)
    with pytest.raises(PaygOfferError):
        await service.update_offer(7, price_per_gb_toman=0)
    current = await service.get_offer(7)
    assert current["enabled"] == 1
    assert current["price_per_gb_toman"] == 2500
