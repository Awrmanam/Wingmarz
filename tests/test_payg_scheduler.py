import asyncio

import config
from scheduler import MonitoringScheduler


def test_rebecca_mode_registers_and_runs_payg_billing(monkeypatch):
    monkeypatch.setattr(config, "PANEL_PROVIDER", "rebecca")
    # Production code clamps the billing cadence to at least one minute.
    monkeypatch.setattr(config, "MONITORING_INTERVAL", 30)

    scheduler = MonitoringScheduler(bot=None)
    calls = []

    async def fake_sync():
        calls.append(True)

    scheduler.sync_payg_billing = fake_sync

    async def scenario():
        await scheduler.start()
        try:
            job = scheduler.scheduler.get_job(scheduler.payg_job_id)
            assert job is not None
            assert scheduler.scheduler.get_job("admin_monitor") is None
            assert job.trigger.interval.total_seconds() == 60
            assert calls == [True]
            status = scheduler.get_status()
            assert status["running"] is True
            assert status["jobs"] >= 1
            assert status["next_run"] is not None
        finally:
            await scheduler.stop()

    asyncio.run(scenario())
