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
    async def send_message(self, chat_id, text, **kwargs): self.sent.append((chat_id, text, kwargs))


class FakeCallback:
    def __init__(self, oid=1, chat=None):
        self.data = f"order_approve_{oid}"
        self.from_user = SimpleNamespace(id=99)
        self.bot = FakeBot(chat or SimpleNamespace(username="Arman_Madani", first_name="Arman", last_name="Madani"))
        self.message, self.answers = FakeMessage(), []
    async def answer(self, text=None, **kwargs): self.answers.append((text, kwargs))


class FakeDB:
    def __init__(self, *, finalize=True, already_reserved=False, order_type="new", stale=True):
        self.order = {"id": 1, "user_id": 42, "plan_id": 2, "status": "pending", "order_type": order_type}
        self.plan = PlanModel(id=2, name="Gold", traffic_limit_bytes=123, time_limit_seconds=86400,
                              max_users=7, allow_incremental_renewal=True)
        self.finalize_ok, self.already_reserved = finalize, already_reserved
        self.finalized = self.reservations = 0
        self.admin = None
        self.reserved = None
        self.cleared = 0
        self.username_updates = 0
        self.clear_ok = True
        self.stale = stale
        import asyncio
        self.lock = asyncio.Lock()
    async def get_order_by_id(self, oid): return self.order
    async def get_plan_by_id(self, pid): return self.plan
    async def reserve_rebecca_provisioning(self, oid, username, password, expire, lease_token, now, lease_seconds):
        async with self.lock:
            self.reservations += 1
            was_reserved = self.already_reserved
            if self.reserved is None:
                self.reserved = (username, password, expire)
            saved_username, saved_password, saved_expire = self.reserved
            if was_reserved and not self.stale:
                return {"should_create": False, "in_progress": True, "recovery": True,
                        "rebecca_username": saved_username, "rebecca_password": saved_password,
                        "rebecca_expire": saved_expire}
            self.already_reserved, self.stale = True, False
            return {"should_create": True, "in_progress": False, "recovery": was_reserved,
                    "rebecca_username": saved_username, "rebecca_password": saved_password,
                    "rebecca_expire": saved_expire, "rebecca_provision_state": "creating"}
    async def update_rebecca_reserved_username(self, *args): self.username_updates += 1; return True
    async def clear_rebecca_reservation(self, *args):
        self.cleared += 1
        if self.clear_ok:
            self.already_reserved = False
        return self.clear_ok
    async def finalize_rebecca_provisioning(self, oid, admin, approved_by, lease_token):
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
    monkeypatch.setattr(config, "REBECCA_SERVICE_IDS", (1, 2))


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
    assert limits == {"data_limit": 123, "expire": 1_086_400, "users_limit": 7, "services": [1, 2]}
    assert password not in {config.BOT_TOKEN, config.MARZBAN_PASSWORD, config.REBECCA_BEARER_TOKEN}
    assert len(password) >= 16 and sent_before_verify == 0
    assert (db.admin.user_id, db.admin.username, db.admin.first_name, db.admin.last_name) == (42, "Arman_Madani", "Arman", "Madani")
    assert db.admin.admin_name == "Arman Madani"
    assert db.order["status"] == "approved"
    assert "https://login.rebecca.example" in callback.bot.sent[0][1]
    assert callback.bot.sent[0][2]["parse_mode"] == "HTML"
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
    assert calls[0][1] == {"data_limit": None, "expire": None, "users_limit": None, "services": [1, 2]}
    assert db.admin.admin_name == "User 42"


def test_rebecca_failure_and_save_failure_are_not_retried(monkeypatch, rebecca):
    db, callback, calls = FakeDB(finalize=False), FakeCallback(), []
    monkeypatch.setattr(sudo, "db", db)
    async def create(*args, **kwargs): calls.append(1); return {"role": "standard", "status": "active"}
    monkeypatch.setattr(sudo.rebecca_api, "create_admin_verified", create)
    run(sudo.order_approve(callback))
    assert db.order["status"] == "pending" and not callback.bot.sent and len(calls) == 1
    db.already_reserved = True
    async def find(username): return {"username": username, "role": "standard", "status": "active", "telegram_id": 42}
    monkeypatch.setattr(sudo.rebecca_api, "find_admin", find)
    run(sudo.order_approve(FakeCallback()))
    assert len(calls) == 1  # durable reservation prevents a second remote create


def test_rebecca_api_failure_leaves_order_pending(monkeypatch, rebecca):
    db, callback = FakeDB(), FakeCallback()
    monkeypatch.setattr(sudo, "db", db)
    async def fail(*args, **kwargs): raise RuntimeError("no secret details")
    monkeypatch.setattr(sudo.rebecca_api, "create_admin_verified", fail)
    run(sudo.order_approve(callback))
    assert db.order["status"] == "pending" and db.finalized == 0 and not callback.bot.sent


def test_missing_services_blocks_before_remote_call(monkeypatch, rebecca):
    db, callback, calls = FakeDB(), FakeCallback(), []
    monkeypatch.setattr(config, "REBECCA_SERVICE_IDS", ())
    monkeypatch.setattr(sudo, "db", db)
    async def create(*args, **kwargs): calls.append(1)
    monkeypatch.setattr(sudo.rebecca_api, "create_admin_verified", create)
    run(sudo.order_approve(callback))
    assert not calls and db.reservations == 0 and db.order["status"] == "pending"


def test_recovery_absent_retries_same_reserved_credentials(monkeypatch, rebecca):
    db, callback, calls = FakeDB(already_reserved=True), FakeCallback(), []
    db.reserved = ("reserved_1234", "reserved-password-123", 2_000_000)
    monkeypatch.setattr(sudo, "db", db)
    async def find(username): return None
    async def create(username, password, telegram_id, **kwargs):
        calls.append((username, password)); return {"username": username, "role": "standard", "status": "active"}
    monkeypatch.setattr(sudo.rebecca_api, "find_admin", find)
    monkeypatch.setattr(sudo.rebecca_api, "create_admin_verified", create)
    run(sudo.order_approve(callback))
    assert calls == [("reserved_1234", "reserved-password-123")]


def test_recovery_existing_finalizes_without_post(monkeypatch, rebecca):
    db, callback, posts = FakeDB(already_reserved=True), FakeCallback(), []
    db.reserved = ("reserved_1234", "reserved-password-123", 2_000_000)
    monkeypatch.setattr(sudo, "db", db)
    async def find(username):
        return {"username": username, "role": "standard", "status": "active", "telegram_id": 42,
                "data_limit": 123, "expire": 2_000_000, "users_limit": 7, "services": [1, 2]}
    async def create(*args, **kwargs): posts.append(1)
    monkeypatch.setattr(sudo.rebecca_api, "find_admin", find)
    monkeypatch.setattr(sudo.rebecca_api, "create_admin_verified", create)
    run(sudo.order_approve(callback))
    assert not posts and db.finalized == 1 and db.order["status"] == "approved"


@pytest.mark.parametrize("status", [400, 401, 403, 422])
def test_definitive_rejection_releases_reservation(monkeypatch, rebecca, status):
    from rebecca_api import RebeccaAPIError
    db, callback = FakeDB(), FakeCallback()
    monkeypatch.setattr(sudo, "db", db)
    async def reject(*args, **kwargs): raise RebeccaAPIError("rejected", status_code=status)
    monkeypatch.setattr(sudo.rebecca_api, "create_admin_verified", reject)
    run(sudo.order_approve(callback))
    assert db.cleared == 1 and db.order["status"] == "pending"


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


def test_two_simultaneous_approvals_only_one_posts(monkeypatch, rebecca):
    import asyncio
    db, first, second, posts = FakeDB(), FakeCallback(), FakeCallback(), []
    monkeypatch.setattr(sudo, "db", db)
    started, release = asyncio.Event(), asyncio.Event()
    async def create(username, password, telegram_id, **kwargs):
        posts.append(username); started.set(); await release.wait()
        return {"username": username, "role": "standard", "status": "active"}
    monkeypatch.setattr(sudo.rebecca_api, "create_admin_verified", create)
    async def scenario():
        task = asyncio.create_task(sudo.order_approve(first))
        await started.wait()
        await sudo.order_approve(second)
        release.set()
        await task
    asyncio.run(scenario())
    assert len(posts) == 1
    assert "صدور در حال انجام است" in second.answers[-1][0]


def test_ambiguous_409_reconciles_without_rotation(monkeypatch, rebecca):
    from rebecca_api import RebeccaConflict
    db, callback = FakeDB(), FakeCallback()
    monkeypatch.setattr(sudo, "db", db)
    async def conflict(*args, **kwargs): raise RebeccaConflict("conflict")
    async def absent(username): return None
    monkeypatch.setattr(sudo.rebecca_api, "create_admin_verified", conflict)
    monkeypatch.setattr(sudo.rebecca_api, "find_admin", absent)
    run(sudo.order_approve(callback))
    assert db.username_updates == 0 and db.cleared == 0
    assert "نامشخص" in callback.answers[-1][0]


def test_six_definite_fresh_conflicts_release_order(monkeypatch, rebecca):
    from rebecca_api import RebeccaConflict
    db, callback, posts = FakeDB(), FakeCallback(), []
    monkeypatch.setattr(sudo, "db", db)
    async def conflict(*args, **kwargs): posts.append(1); raise RebeccaConflict("conflict")
    async def other(username):
        return {"username": username, "role": "standard", "status": "active", "telegram_id": 999}
    monkeypatch.setattr(sudo.rebecca_api, "create_admin_verified", conflict)
    monkeypatch.setattr(sudo.rebecca_api, "find_admin", other)
    run(sudo.order_approve(callback))
    assert len(posts) == 6 and db.username_updates == 5 and db.cleared == 1
    assert not db.already_reserved


def test_failed_definitive_clear_reports_local_attention(monkeypatch, rebecca):
    from rebecca_api import RebeccaAPIError
    db, callback = FakeDB(), FakeCallback(); db.clear_ok = False
    monkeypatch.setattr(sudo, "db", db)
    async def reject(*args, **kwargs): raise RebeccaAPIError("bad", status_code=400)
    monkeypatch.setattr(sudo.rebecca_api, "create_admin_verified", reject)
    run(sudo.order_approve(callback))
    assert db.cleared == 1 and "محلی" in callback.answers[-1][0]

@pytest.mark.parametrize("mapping, expected", [("1", [1]), ("2", [2]), ("1,2,1", [1, 2])])
def test_rebecca_provisioning_uses_each_plans_mapping(monkeypatch, rebecca, mapping, expected):
    db, callback, calls = FakeDB(), FakeCallback(), []
    db.plan.rebecca_service_ids = mapping
    monkeypatch.setattr(sudo, "db", db)
    async def create(*args, **kwargs):
        calls.append(kwargs["services"])
        return {"username": args[0], "role": "standard", "status": "active"}
    monkeypatch.setattr(sudo.rebecca_api, "create_admin_verified", create)
    run(sudo.order_approve(callback))
    assert calls == [expected]


def test_invalid_plan_mapping_blocks_before_post(monkeypatch, rebecca):
    db, callback = FakeDB(), FakeCallback()
    db.plan.rebecca_service_ids = "1,bad"
    monkeypatch.setattr(sudo, "db", db)
    async def create(*args, **kwargs): pytest.fail("POST must not be attempted")
    monkeypatch.setattr(sudo.rebecca_api, "create_admin_verified", create)
    run(sudo.order_approve(callback))
    assert "نامعتبر" in callback.answers[-1][0]


def test_credential_username_with_underscores_reaches_telegram_exactly(monkeypatch, rebecca):
    db = FakeDB()
    callback = FakeCallback(chat=SimpleNamespace(username="armanstore2_support", first_name="Arman", last_name=None))
    db.plan.rebecca_service_ids = "1"
    monkeypatch.setattr(sudo, "db", db)
    monkeypatch.setattr(sudo.secrets, "randbelow", lambda _: 7090)
    async def create(*args, **kwargs):
        return {"username": args[0], "role": "standard", "status": "active"}
    monkeypatch.setattr(sudo.rebecca_api, "create_admin_verified", create)
    run(sudo.order_approve(callback))
    text = callback.bot.sent[0][1]
    assert "armanstore2_support_8090" in text
    assert "<pre>" not in text and "```" not in text and "Gold" in text
