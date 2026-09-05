import asyncio

from models.schemas import PlanModel
from service_marketplace_service import ServiceMarketplaceService


def _plan(plan_id: int, days: int | None, services: str = "10", name: str = "Plan") -> PlanModel:
    return PlanModel(
        id=plan_id,
        name=name,
        time_limit_seconds=None if days is None else days * 86400,
        traffic_limit_bytes=100 * 1024**3,
        max_users=10,
        price=100_000,
        is_active=True,
        rebecca_service_ids=services,
    )


def test_plan_service_ids_are_provider_ids_not_catalog_ids():
    service = ServiceMarketplaceService(":memory:")
    assert service.plan_service_ids(_plan(1, 30, "12,34")) == [12, 34]
    assert service.plan_service_ids(_plan(2, 30, "")) == []


def test_duration_groups_are_generated_automatically():
    groups = ServiceMarketplaceService.group_plans_by_duration([
        _plan(1, 30),
        _plan(2, 30),
        _plan(3, 90),
        _plan(4, 360),
    ])
    assert [item.label for item in groups] == ["1 ماهه", "3 ماهه", "1 ساله"]
    assert [len(item.plans) for item in groups] == [2, 1, 1]


def test_non_month_duration_keeps_day_label():
    groups = ServiceMarketplaceService.group_plans_by_duration([_plan(1, 7)])
    assert groups[0].label == "7 روزه"


def test_duration_group_toggle_persists(tmp_path):
    service = ServiceMarketplaceService(str(tmp_path / "market.db"))

    async def scenario():
        assert await service.duration_groups_enabled() is True
        await service.set_duration_groups_enabled(False)
        assert await service.duration_groups_enabled() is False
        await service.set_duration_groups_enabled(True)
        assert await service.duration_groups_enabled() is True

    asyncio.run(scenario())
