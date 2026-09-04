import asyncio
from types import SimpleNamespace
import time

import aiosqlite
import pytest

import config
from operations_service import OperationsError, OperationsService


def run(coro):
    return asyncio.run(coro)


def make_service(tmp_path):
    return OperationsService(str(tmp_path / "ops.db"))


def create_orders_table(path):
    async def _create():
        async with aiosqlite.connect(path) as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    status TEXT DEFAULT 'pending'
                )
                """
            )
            await conn.commit()
    run(_create())


def test_schema_is_migration_safe(tmp_path):
    service = make_service(tmp_path)
    run(service.ensure_schema())
    run(service.ensure_schema())

    async def inspect():
        async with aiosqlite.connect(service.db_path) as conn:
            for table in (
                "discount_codes",
                "discount_redemptions",
                "bot_admins",
                "menu_settings",
                "trial_settings",
                "trial_issues",
            ):
                async with conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
                ) as cur:
                    assert await cur.fetchone() is not None
    run(inspect())


def test_discount_validation_and_quote(tmp_path):
    service = make_service(tmp_path)
    create_orders_table(service.db_path)
    discount_id = run(service.create_discount(
        code="return10",
        kind="percent",
        value=10,
        min_order=100_000,
        max_uses=10,
        per_user_limit=1,
        created_by=1,
    ))
    assert discount_id > 0
    quote = run(service.quote_discount("RETURN10", 42, 500_000))
    assert quote.original_price == 500_000
    assert quote.discount_amount == 50_000
    assert quote.final_price == 450_000


def test_fixed_discount_is_clamped_to_order_price(tmp_path):
    service = make_service(tmp_path)
    create_orders_table(service.db_path)
    run(service.create_discount(code="FREEISH", kind="fixed", value=999_999))
    quote = run(service.quote_discount("FREEISH", 1, 50_000))
    assert quote.final_price == 0
    assert quote.discount_amount == 50_000


def test_expired_and_minimum_order_discounts_are_rejected(tmp_path):
    service = make_service(tmp_path)
    create_orders_table(service.db_path)
    with pytest.raises(OperationsError):
        run(service.create_discount(
            code="OLD10",
            kind="percent",
            value=10,
            expires_at=int(time.time()) - 1,
        ))
    run(service.create_discount(code="MIN10", kind="percent", value=10, min_order=1000))
    with pytest.raises(OperationsError):
        run(service.quote_discount("MIN10", 1, 999))


def test_redemption_limits_ignore_rejected_orders(tmp_path):
    service = make_service(tmp_path)
    create_orders_table(service.db_path)
    discount_id = run(service.create_discount(
        code="ONCE",
        kind="percent",
        value=20,
        max_uses=1,
        per_user_limit=1,
    ))
    quote = run(service.quote_discount("ONCE", 7, 1000))

    async def seed(status):
        async with aiosqlite.connect(service.db_path) as conn:
            cur = await conn.execute("INSERT INTO orders(user_id,status) VALUES(?,?)", (7, status))
            await conn.commit()
            return int(cur.lastrowid)

    rejected_order = run(seed("rejected"))
    run(service.record_redemption(quote, 7, rejected_order))
    # Rejected orders release the code slot.
    again = run(service.quote_discount("ONCE", 7, 1000))
    assert again.discount_id == discount_id


def test_redemption_limit_blocks_active_order(tmp_path):
    service = make_service(tmp_path)
    create_orders_table(service.db_path)
    run(service.create_discount(code="LIMIT1", kind="percent", value=20, max_uses=1, per_user_limit=1))
    quote = run(service.quote_discount("LIMIT1", 7, 1000))

    async def seed():
        async with aiosqlite.connect(service.db_path) as conn:
            cur = await conn.execute("INSERT INTO orders(user_id,status) VALUES(7,'pending')")
            await conn.commit()
            return int(cur.lastrowid)

    order_id = run(seed())
    run(service.record_redemption(quote, 7, order_id))
    with pytest.raises(OperationsError):
        run(service.quote_discount("LIMIT1", 8, 1000))


def test_menu_visibility_defaults_true_and_persists(tmp_path):
    service = make_service(tmp_path)
    assert run(service.menu_is_visible("cc:test")) is True
    run(service.set_menu_visible("cc:test", False))
    assert run(service.menu_is_visible("cc:test")) is False
    run(service.set_menu_visible("cc:test", True))
    assert run(service.menu_is_visible("cc:test")) is True


def test_runtime_admins_persist_and_sync(tmp_path, monkeypatch):
    service = make_service(tmp_path)
    monkeypatch.setattr(service, "_db_path", service.db_path)
    original = list(config.SUDO_ADMINS)
    try:
        config.SUDO_ADMINS[:] = list(service.base_sudo_ids)
        run(service.add_runtime_admin(987654321, service.base_sudo_ids[0] if service.base_sudo_ids else 1))
        assert 987654321 in config.SUDO_ADMINS
        run(service.set_runtime_admin_active(987654321, False))
        assert 987654321 not in config.SUDO_ADMINS
        run(service.set_runtime_admin_active(987654321, True))
        assert 987654321 in config.SUDO_ADMINS
        run(service.remove_runtime_admin(987654321))
        assert 987654321 not in config.SUDO_ADMINS
    finally:
        config.SUDO_ADMINS[:] = original


def test_base_sudo_cannot_be_removed(tmp_path):
    service = make_service(tmp_path)
    if not service.base_sudo_ids:
        pytest.skip("No configured SUDO in test environment")
    with pytest.raises(OperationsError):
        run(service.remove_runtime_admin(service.base_sudo_ids[0]))


def test_trial_defaults_and_cooldown(tmp_path):
    service = make_service(tmp_path)
    settings = run(service.get_trial_settings())
    assert settings["enabled"] is False
    assert settings["traffic_bytes"] > 0
    assert settings["duration_seconds"] >= 60

    run(service.set_trial_setting("enabled", True))
    run(service.set_trial_setting("cooldown_seconds", 3600))
    run(service.record_trial(
        user_id=5,
        provider="rebecca",
        provider_username="test_5_abc",
        service_id=1,
        subscription_url="https://example.test/sub",
        expire_at=int(time.time()) + 3600,
    ))
    assert run(service.trial_wait_seconds(5)) > 0


def test_rebecca_trial_uses_service_and_returns_subscription(tmp_path, monkeypatch):
    service = make_service(tmp_path)
    run(service.set_trial_setting("enabled", True))
    run(service.set_trial_setting("cooldown_seconds", 0))
    run(service.set_trial_setting("rebecca_service_id", 44))
    monkeypatch.setattr(config, "PANEL_PROVIDER", "rebecca")

    import rebecca_api as rebecca_module

    captured = {}

    async def fake_request(method, path, **kwargs):
        captured["method"] = method
        captured["path"] = path
        captured["json"] = kwargs.get("json")
        payload = kwargs["json"]
        return {
            "username": payload["username"],
            "service_id": payload["service_id"],
            "subscription_url": "https://panel.test/sub/key",
            "links": ["vless://example"],
        }

    monkeypatch.setattr(rebecca_module.rebecca_api, "_request", fake_request)
    result = run(service.issue_trial(555))
    assert captured["method"] == "POST"
    assert captured["path"] == "/api/user"
    assert captured["json"]["service_id"] == 44
    assert captured["json"]["telegram_id"] == "555"
    assert result["subscription_url"] == "https://panel.test/sub/key"
    assert result["service_id"] == 44


def test_marzban_trial_discovers_inbound_and_creates_user(tmp_path, monkeypatch):
    service = make_service(tmp_path)
    run(service.set_trial_setting("enabled", True))
    run(service.set_trial_setting("cooldown_seconds", 0))
    monkeypatch.setattr(config, "PANEL_PROVIDER", "marzban")

    import marzban_api as marzban_module

    class Response:
        def __init__(self, status_code, data):
            self.status_code = status_code
            self._data = data

        def json(self):
            return self._data

    calls = []

    async def fake_request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        if method == "GET":
            return Response(200, {"vless": [{"tag": "VLESS TCP", "protocol": "vless"}]})
        payload = kwargs["json"]
        return Response(201, {
            "username": payload["username"],
            "subscription_url": "https://marzban.test/sub/key",
            "links": ["vless://example"],
        })

    monkeypatch.setattr(marzban_module.marzban_api, "_request", fake_request)
    result = run(service.issue_trial(777))
    assert calls[0][0] == "GET"
    assert calls[1][0] == "POST"
    assert calls[1][2]["json"]["inbounds"] == {"vless": ["VLESS TCP"]}
    assert result["subscription_url"] == "https://marzban.test/sub/key"
