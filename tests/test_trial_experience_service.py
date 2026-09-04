import asyncio
import time

import aiosqlite
import pytest

from operations_service import OperationsError
from trial_experience_service import TrialExperienceService


def run(coro):
    return asyncio.run(coro)


def test_username_validation_is_exact_and_lowercase():
    assert TrialExperienceService.validate_username("  Arman_Panel-1  ") == "arman_panel-1"
    assert TrialExperienceService.validate_username("abc") == "abc"


@pytest.mark.parametrize(
    "value",
    ["ab", "a b c", "آرمان", "user!", "_starts_wrong", "a" * 33, ""],
)
def test_username_validation_rejects_invalid_values(value):
    with pytest.raises(OperationsError):
        TrialExperienceService.validate_username(value)


def test_schema_is_migration_safe_and_separate(tmp_path):
    path = str(tmp_path / "trial.db")
    service = TrialExperienceService(path)
    run(service.ensure_schema())
    run(service.ensure_schema())

    async def inspect():
        async with aiosqlite.connect(path) as conn:
            for table in (
                "order_preferences",
                "order_issue_locks",
                "trial_plan_access",
                "panel_trial_settings",
                "panel_trial_issues",
            ):
                async with conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
                ) as cur:
                    assert await cur.fetchone() is not None
                async with conn.execute(f"PRAGMA foreign_key_list({table})") as cur:
                    assert await cur.fetchall() == []

    run(inspect())


def test_order_username_round_trip(tmp_path):
    path = str(tmp_path / "order_pref.db")
    service = TrialExperienceService(path)
    run(service.save_order_username(41, "my_panel"))
    assert run(service.get_order_username(41)) == "my_panel"
    run(service.save_order_username(41, "new_panel"))
    assert run(service.get_order_username(41)) == "new_panel"


def test_panel_trial_defaults_and_updates(tmp_path):
    path = str(tmp_path / "settings.db")
    service = TrialExperienceService(path)
    settings = run(service.get_panel_trial_settings())
    assert settings["enabled"] is False
    assert settings["traffic_bytes"] == 10 * 1024**3
    assert settings["duration_seconds"] == 24 * 60 * 60
    assert settings["max_users"] == 5
    assert settings["cooldown_seconds"] == 7 * 24 * 60 * 60

    run(service.set_panel_trial_setting("enabled", True))
    run(service.set_panel_trial_setting("max_users", 9))
    updated = run(service.get_panel_trial_settings())
    assert updated["enabled"] is True
    assert updated["max_users"] == 9


def test_unknown_panel_setting_is_rejected(tmp_path):
    service = TrialExperienceService(str(tmp_path / "bad.db"))
    with pytest.raises(OperationsError):
        run(service.set_panel_trial_setting("password", "secret"))


def test_plan_access_defaults_enabled_and_can_be_disabled(tmp_path):
    service = TrialExperienceService(str(tmp_path / "plans.db"))
    assert run(service.plan_enabled(7, "panel")) is True
    assert run(service.plan_enabled(7, "config")) is True
    run(service.set_plan_enabled(7, "panel", False))
    assert run(service.plan_enabled(7, "panel")) is False
    assert run(service.plan_enabled(7, "config")) is True


def test_invalid_trial_type_is_safe(tmp_path):
    service = TrialExperienceService(str(tmp_path / "types.db"))
    assert run(service.plan_enabled(1, "other")) is False
    with pytest.raises(OperationsError):
        run(service.set_plan_enabled(1, "other", True))


def test_panel_trial_cooldown_uses_latest_issue(tmp_path):
    path = str(tmp_path / "cooldown.db")
    service = TrialExperienceService(path)
    run(service.ensure_schema())
    run(service.set_panel_trial_setting("cooldown_seconds", 3600))

    async def insert_issue():
        async with aiosqlite.connect(path) as conn:
            await conn.execute(
                """
                INSERT INTO panel_trial_issues(
                    user_id,provider,provider_username,plan_id,expire_at,created_at
                ) VALUES(?,?,?,?,?,?)
                """,
                (100, "rebecca", "trial_user", 1, int(time.time()) + 7200, int(time.time())),
            )
            await conn.commit()

    run(insert_issue())
    remaining = run(service.panel_trial_wait_seconds(100))
    assert 3500 <= remaining <= 3600
    assert run(service.panel_trial_wait_seconds(101)) == 0


def test_order_issue_lock_reuses_password_after_stale_lease(tmp_path):
    path = str(tmp_path / "locks.db")
    service = TrialExperienceService(path)
    run(service.ensure_schema())
    password, recovery = run(service._acquire_order_lock(5, "wanted_name"))
    assert recovery is False
    assert password

    async def make_stale():
        async with aiosqlite.connect(path) as conn:
            await conn.execute("UPDATE order_issue_locks SET lease_at=? WHERE order_id=5", (int(time.time()) - 1000,))
            await conn.commit()

    run(make_stale())
    password2, recovery2 = run(service._acquire_order_lock(5, "wanted_name"))
    assert recovery2 is True
    assert password2 == password


def test_completed_order_lock_cannot_reissue(tmp_path):
    path = str(tmp_path / "done.db")
    service = TrialExperienceService(path)
    run(service.ensure_schema())
    run(service._acquire_order_lock(9, "wanted_name"))
    run(service._complete_order_lock(9))
    with pytest.raises(OperationsError):
        run(service._acquire_order_lock(9, "wanted_name"))
