"""Backend KYC/trust authorization, independent of Telegram UI."""
import pytest
import kyc_service as kyc_mod
import trust_service as trust_mod


@pytest.mark.asyncio
async def test_kyc_review_requires_backend_operator_and_is_single_use(tmp_path,monkeypatch):
    monkeypatch.setattr(kyc_mod.config,"SUDO_ADMINS",[99])
    service=kyc_mod.KycService(str(tmp_path/"kyc.db"))
    await service.submit(42,"Example Customer","fake-photo-id")
    with pytest.raises(PermissionError,match="reviewer"):
        await service.approve(42,42)
    with pytest.raises(PermissionError,match="reviewer"):
        await service.reject(42,42)
    assert (await service.get_profile(42))["status"]=="pending"
    assert await service.approve(42,99) is True
    assert await service.approve(42,99) is False
    assert await service.reject(42,99) is False


@pytest.mark.asyncio
async def test_manual_trust_grant_and_revocation_require_backend_operator(tmp_path,monkeypatch):
    monkeypatch.setattr(trust_mod.config,"SUDO_ADMINS",[99])
    service=trust_mod.TrustService(str(tmp_path/"trust.db"))
    with pytest.raises(PermissionError,match="operator"):
        await service.set_manual_trusted(42,True,42)
    assert not await service.is_manually_trusted(42)
    await service.set_manual_trusted(42,True,99)
    assert await service.is_manually_trusted(42)
    with pytest.raises(PermissionError,match="operator"):
        await service.set_manual_trusted(42,False,42)
    assert await service.is_manually_trusted(42)
    await service.set_manual_trusted(42,False,99)
    assert not await service.is_manually_trusted(42)


@pytest.mark.asyncio
async def test_auto_approve_requires_backend_operator_even_for_trusted_customer(tmp_path,monkeypatch):
    monkeypatch.setattr(trust_mod.config,"SUDO_ADMINS",[99])
    service=trust_mod.TrustService(str(tmp_path/"auto.db"))
    await service.set_manual_trusted(42,True,99)
    with pytest.raises(PermissionError,match="auto-approve"):
        await service.set_auto_approve(42,True)
    with pytest.raises(PermissionError,match="auto-approve"):
        await service.set_auto_approve(42,True,actor_id=42)
    assert not await service.auto_approve_enabled(42)
    await service.set_auto_approve(42,True,actor_id=99)
    assert await service.auto_approve_enabled(42)
