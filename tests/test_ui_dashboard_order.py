"""Editable dashboard ordering is additive, stable and cannot hide its recovery."""
import asyncio

import aiosqlite
import pytest

from operations_service import OperationsError, OperationsService


DEFAULTS = [
    "ops:hub:panels", "ops:hub:sales", "ops:hub:finance",
    "ops:hub:customers", "ops:hub:monitor", "ops:hub:settings",
]


def test_menu_settings_migrates_legacy_sqlite_and_preserves_visibility(tmp_path):
    path = str(tmp_path / "legacy.sqlite")

    async def scenario():
        async with aiosqlite.connect(path) as conn:
            await conn.execute(
                "CREATE TABLE menu_settings(callback_data TEXT PRIMARY KEY,"
                "is_visible INTEGER NOT NULL DEFAULT 1,"
                "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
            )
            await conn.execute(
                "INSERT INTO menu_settings(callback_data,is_visible)"
                " VALUES('ops:hub:sales',0)"
            )
            await conn.commit()
        service = OperationsService(path)
        assert await service.get_menu_order(DEFAULTS) == DEFAULTS
        assert not await service.menu_is_visible("ops:hub:sales")
        moved = await service.move_menu("ops:hub:monitor",-1,DEFAULTS)
        assert moved[3] == "ops:hub:monitor"
        assert moved[4] == "ops:hub:customers"
        assert not await service.menu_is_visible("ops:hub:sales")
        await service.set_menu_visible("ops:hub:sales",True)
        assert await service.menu_is_visible("ops:hub:sales")
        assert await service.get_menu_order(DEFAULTS) == moved
        with pytest.raises(OperationsError,match="نباید پنهان"):
            await service.set_menu_visible("ops:hub:settings",False)
        assert await service.menu_is_visible("ops:hub:settings")

    asyncio.run(scenario())


def test_concurrent_dashboard_moves_remain_valid_permutations(tmp_path):
    service = OperationsService(str(tmp_path / "positions.sqlite"))

    async def scenario():
        await asyncio.gather(
            service.move_menu("ops:hub:finance", -1, DEFAULTS),
            service.move_menu("ops:hub:customers", 1, DEFAULTS),
        )
        ordered = await service.get_menu_order(DEFAULTS)
        assert len(ordered) == len(DEFAULTS)
        assert set(ordered) == set(DEFAULTS)
        for item in ordered:
            assert ordered.count(item) == 1
        with pytest.raises(OperationsError):
            await service.move_menu("order_approve_42", 1, DEFAULTS)

    asyncio.run(scenario())


def test_dashboard_visibility_is_not_changed_by_reordering(tmp_path):
    service = OperationsService(str(tmp_path / "hidden.sqlite"))

    async def scenario():
        await service.set_menu_visible("ops:hub:finance",False)
        before = await service.get_menu_order(DEFAULTS)
        after = await service.move_menu("ops:hub:finance",1,DEFAULTS)
        assert before != after
        assert not await service.menu_is_visible("ops:hub:finance")

    asyncio.run(scenario())
