import asyncio
from pathlib import Path

import config
from payg_offers_service import PaygOfferService
from payg_service import payg_service
from rebecca_catalog import import_services_atomic


def _run(coro):
    return asyncio.run(coro)


def test_legacy_provider_payg_migrates_to_service_offer(tmp_path, monkeypatch):
    db_path = str(tmp_path / "legacy-payg.sqlite")
    monkeypatch.setattr(config, "DATABASE_PATH", db_path)
    monkeypatch.setattr(config, "REBECCA_SERVICE_IDS", (7,))

    _run(payg_service.ensure_schema())
    _run(payg_service.update_settings(
        "rebecca",
        service_ids="7",
        price_per_gb_toman=8000,
        min_topup_toman=150000,
        max_users=25,
    ))
    _run(payg_service.set_enabled("rebecca", True))

    offers = PaygOfferService()
    _run(offers.ensure_schema())
    offer = _run(offers.get_offer(7))

    assert offer is not None
    assert offer["service_id"] == 7
    assert offer["enabled"] == 1
    assert offer["price_per_gb_toman"] == 8000
    assert offer["min_topup_toman"] == 150000
    assert offer["max_users"] == 25


def test_each_rebecca_service_has_independent_payg_tariff(tmp_path, monkeypatch):
    db_path = str(tmp_path / "service-payg.sqlite")
    monkeypatch.setattr(config, "DATABASE_PATH", db_path)
    monkeypatch.setattr(config, "REBECCA_SERVICE_IDS", ())

    _run(payg_service.ensure_schema())
    _run(import_services_atomic([
        {"service_id": 7, "display_name": "OpenVPN", "provider_name": "openvpn"},
        {"service_id": 9, "display_name": "V2Ray", "provider_name": "v2ray"},
    ], source_username="catalog-test"))

    offers = PaygOfferService()
    _run(offers.ensure_offer(7))
    _run(offers.ensure_offer(9))
    _run(offers.update_offer(7, price_per_gb_toman=8000, min_topup_toman=100000, max_users=20))
    _run(offers.update_offer(9, price_per_gb_toman=12000, min_topup_toman=200000, max_users=50))
    _run(offers.update_offer(7, enabled=True))
    _run(offers.update_offer(9, enabled=True))

    openvpn = _run(offers.get_offer(7))
    v2ray = _run(offers.get_offer(9))
    visible = _run(offers.list_offers(enabled_only=True))

    assert openvpn["price_per_gb_toman"] == 8000
    assert v2ray["price_per_gb_toman"] == 12000
    assert {item["display_name"] for item in visible} == {"OpenVPN", "V2Ray"}


def test_storefront_is_service_first_and_admin_no_longer_needs_raw_service_ids():
    entry = Path("handlers/payg_entry.py").read_text(encoding="utf-8")
    offers = Path("handlers/payg_offers.py").read_text(encoding="utf-8")

    assert "render_service_storefront" in entry
    assert "تعرفه‌های PAYG بر اساس سرویس" in entry
    assert "paygsvc:view:" in offers
    assert "پرداخت به‌ازای مصرف" in offers
    assert "Service ID را فقط در «سرویس‌های Rebecca» تعریف می‌کنید" in offers
