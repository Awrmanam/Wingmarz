"""Regression checks for PAYG settings: no invalid config may be persisted."""
import pytest
from payg_service import PaygError, PaygService


@pytest.mark.asyncio
async def test_invalid_enabled_rate_does_not_persist(tmp_path):
    service = PaygService(str(tmp_path / "payg.db"))
    await service.ensure_schema()
    before = await service.get_settings()
    with pytest.raises(PaygError):
        await service.update_settings(enabled=True, price_per_gb_toman=0, service_ids="1")
    assert await service.get_settings() == before


@pytest.mark.asyncio
async def test_invalid_enabled_service_ids_do_not_persist(tmp_path):
    service = PaygService(str(tmp_path / "payg.db"))
    await service.ensure_schema()
    before = await service.get_settings()
    with pytest.raises(PaygError):
        await service.update_settings(enabled=True, price_per_gb_toman=2000, service_ids="")
    assert await service.get_settings() == before


@pytest.mark.asyncio
async def test_valid_disabled_rate_change(tmp_path):
    service = PaygService(str(tmp_path / "payg.db"))
    await service.ensure_schema()
    updated = await service.update_settings(price_per_gb_toman=2000)
    assert updated["price_per_gb_toman"] == 2000
    assert updated["enabled"] == 0
