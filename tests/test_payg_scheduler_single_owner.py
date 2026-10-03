import asyncio

import config
from scheduler import MonitoringScheduler


def test_rebecca_monitoring_scheduler_does_not_duplicate_payg_job(monkeypatch):
    monkeypatch.setattr(config, "PANEL_PROVIDER", "rebecca")

    scheduler = MonitoringScheduler(bot=None)

    async def scenario():
        await scheduler.start()
        try:
            # PAYG billing is owned by handlers.payg_entry router startup.
            # The shared MonitoringScheduler must not register a second billing job.
            assert scheduler.scheduler.get_job("payg_billing_job") is None
            status = scheduler.get_status()
            assert status["running"] is True
        finally:
            await scheduler.stop()

    asyncio.run(scenario())
