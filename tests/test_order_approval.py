from types import SimpleNamespace

import pytest

import config
import handlers.sudo_handlers as sudo
from models.schemas import AdminModel, PlanModel


class FakeMessage:
    def __init__(self): self.edits = []
    async def edit_text(self, text, **kwargs): self.edits.append(text)


class FakeBot:
    def __init__(self, chat): self.chat, self.sent = chat, []
    async def get_chat(self, user_id): return self.chat
    async def send_message(self, chat_id, text): self.sent.append((chat_id, text))


class FakeCallback:
    def __init__(self, oid=1, chat=None):
        self.data = f"order_approve_{oid}"
        self.from_user = SimpleNamespace(id=99)
        self.bot = FakeBot(chat or SimpleNamespace(username="Arman_Madani", first_name="Arman", last_name="Madani"))
        self.message, self.answers = FakeMessage(), []
    async def answer(self, text=None, **kwargs): self.answers.append((text, kwargs))


class FakeDB:
    def __init__(self, *, finalize=True, already_reserved=False, order_type="new"):
        self.order = {"id": 1, "user_id": 42, "plan_id": 2, "status": "pending", "order_type": order_type}
        self.plan = PlanModel(id=2, name="Gold", traffic_limit_bytes=123, time_limit_seconds=86400,
                              max_users=7, allow_incremental_renewal=True)
        self.finalize_ok, self.already_reserved = finalize, already_reserved
        self.finalized = self.reservations = 0
        self.admin = None
    async def get_order_by_id(self, oid): return self.order
    async def get_plan_by_id(self, pid): return self.plan
    async def reserve_rebecca_provisioning(self, oid, username, password):
        self.reservations += 1
        return {"should_create": not self.already_reserved, "rebecca_username": username,
                "rebecca_password": password, "rebecca_provision_state": "creating"}
    async def update_rebecca_reserved_username(self, *args): return True
    async def finalize_rebecca_provisioning(self, oid, admin, approved_by):
        self.finalized += 1
        if self.finalize_ok:
            self.order["status"], self.admin = "approved", admin
        return self.finalize_ok
    async def get_admin_by_marzban_username(self, username): return self.admin
    async def get_setting(self, key): return None
    async def add_admin(self, admin): self.admin = admin; return True
    async def get_admins_for_user(self, user_id):
        return [self.admin.model_copy(update={"id": 5})] if self.admin else []
    async def update_order(self, oid, **fields): self.order.update(fields); return True


@pytest.fixture
def rebecca(monkeypatch):
    monkeypatch.setattr(config, "PANEL_PROVIDER", "rebecca")
    monkeypatch.setattr(config, "SUDO_ADMINS", [99])
    monkeypatch.setattr(config, "REBECCA_URL", "https://rebecca.example")
    monkeypatch.setattr(config, "REBECCA_LOGIN_URL", "https://login.rebecca.example")
    monkeypatch.setattr(config, "BOT_TOKEN", "bot-secret")
    monkeypatch.setattr(config, "MARZBAN_PASSWORD", "marzban-secret")
    monkeypatch.setattr(config, "REBECCA_BEARER_TOKEN", "rebecca-secret")


def run(coro):
    import asyncio
    return asyncio.run(coro)


def test_real_rebecca_approval_maps_identity_plan_and_notifies_after_verification(monkeypatch, rebecca):
    db, callback, calls = FakeDB(), FakeCallback(), []
    monkeypatch.setattr(sudo, "db", db)
    monkeypatch.setattr(sudo.time, "time", lambda: 1_000_000)

    async def create(username, password, telegram_id, **limits):
        calls.append((username, password, telegram_id, limits, len(callback.bot.sent)))
        return {"username": username, "role": "standard", "status": "active"}
    monkeypatch.setattr(sudo.rebecca_api, "create_admin_verified", create)
    run(sudo.order_approve(callback))

    username, password, telegram_id, limits, sent_before_verify = calls[0]
    assert username.startswith("arman_madani_") and username.replace("arman_madani_", "").isdigit()
    assert telegram_id == 42
    assert limits == {"data_limit": 123, "expire": 1_086_400, "users_limit": 7}
    assert password not in {config.BOT_TOKEN, config.MARZBAN_PASSWORD, config.REBECCA_BEARER_TOKEN}
    assert len(password) >= 16 and sent_before_verify == 0
    assert (db.admin.user_id, db.admin.username, db.admin.first_name, db.admin.last_name) == (42, "Arman_Madani", "Arman", "Madani")
    assert db.admin.admin_name == "Arman Madani"
    assert db.order["status"] == "approved"
    assert "https://login.rebecca.example" in callback.bot.sent[0][1]
    assert config.MARZBAN_URL not in callback.bot.sent[0][1]


def test_rebecca_fallback_username_and_unlimited_mapping(monkeypatch, rebecca):
    db = FakeDB(); db.plan.traffic_limit_bytes = db.plan.time_limit_seconds = db.plan.max_users = None
    callback, calls = FakeCallback(chat=SimpleNamespace(username=None, first_name=None, last_name=None)), []
    monkeypatch.setattr(sudo, "db", db)
    async def create(username, password, telegram_id, **limits):
        calls.append((username, limits)); return {"username": username, "role": "standard", "status": "active"}
    monkeypatch.setattr(sudo.rebecca_api, "create_admin_verified", create)
    run(sudo.order_approve(callback))
    assert calls[0][0].startswith("user_42_")
    assert calls[0][1] == {"data_limit": None, "expire": None, "users_limit": None}
    assert db.admin.admin_name == "User 42"


def test_rebecca_failure_and_save_failure_are_not_retried(monkeypatch, rebecca):
    db, callback, calls = FakeDB(finalize=False), FakeCallback(), []
    monkeypatch.setattr(sudo, "db", db)
    async def create(*args, **kwargs): calls.append(1); return {"role": "standard", "status": "active"}
    monkeypatch.setattr(sudo.rebecca_api, "create_admin_verified", create)
    run(sudo.order_approve(callback))
    assert db.order["status"] == "pending" and not callback.bot.sent and len(calls) == 1
    db.already_reserved = True
    run(sudo.order_approve(FakeCallback()))
    assert len(calls) == 1  # durable reservation prevents a second remote create


def test_rebecca_api_failure_leaves_order_pending(monkeypatch, rebecca):
    db, callback = FakeDB(), FakeCallback()
    monkeypatch.setattr(sudo, "db", db)
    async def fail(*args, **kwargs): raise RuntimeError("no secret details")
    monkeypatch.setattr(sudo.rebecca_api, "create_admin_verified", fail)
    run(sudo.order_approve(callback))
    assert db.order["status"] == "pending" and db.finalized == 0 and not callback.bot.sent


def test_rebecca_renewal_is_blocked(monkeypatch, rebecca):
    db, callback = FakeDB(order_type="renew"), FakeCallback()
    monkeypatch.setattr(sudo, "db", db)
    run(sudo.order_approve(callback))
    assert db.order["status"] == "pending"
    assert "پشتیبانی" in callback.answers[-1][0]


def test_normal_modules_keep_original_marzban_singleton():
    import bot, scheduler, handlers.admin_handlers as admin_handlers
    from marzban_api import marzban_api
    assert bot.marzban_api is marzban_api
    assert scheduler.marzban_api is marzban_api
    assert admin_handlers.marzban_api is marzban_api


def test_marzban_new_order_keeps_original_create_path(monkeypatch):
    db, callback, calls = FakeDB(), FakeCallback(), []
    monkeypatch.setattr(config, "PANEL_PROVIDER", "marzban")
    monkeypatch.setattr(config, "SUDO_ADMINS", [99])
    monkeypatch.setattr(sudo, "db", db)
    async def exists(username): return False
    async def create(username, password, telegram_id, is_sudo):
        calls.append((username, telegram_id, is_sudo)); return True
    monkeypatch.setattr(sudo.marzban_api, "admin_exists", exists)
    monkeypatch.setattr(sudo.marzban_api, "create_admin", create)
    run(sudo.order_approve(callback))
    assert calls == [("panel42", 42, False)]
    assert db.order["status"] == "approved"
    assert db.admin.marzban_username == "panel42"
