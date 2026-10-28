import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from discord.ext.tasks import ExponentialBackoff
from pymongo.errors import AutoReconnect, ServerSelectionTimeoutError


@pytest.mark.parametrize("worker_name", ["scheduler", "expiry", "pause", "sticky"])
@pytest.mark.parametrize("error_type", [AutoReconnect, ServerSelectionTimeoutError])
def test_worker_recovers_after_database_connection_failure(
    import_app, monkeypatch, worker_name, error_type
):
    monkeypatch.setattr(ExponentialBackoff, "delay", lambda self: 0)

    async def check():
        bot = SimpleNamespace(main_guild_id=1, wait_until_ready=AsyncMock())
        recovered = asyncio.Event()
        attempts = 0

        async def query(*args):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise error_type("temporary database outage")
            recovered.set()
            return []

        if worker_name == "scheduler":
            module = import_app("app.services.schedule_service")
            monkeypatch.setattr(module, "get_active_schedules", query)
            worker = module.ScheduleService(bot).scheduler_loop
        elif worker_name == "expiry":
            module = import_app("app.services.ghosty_tempchannel_service")
            monkeypatch.setattr(module.repository, "get_deleted_pending_notifications", query)
            monkeypatch.setattr(module.repository, "get_due_warnings", AsyncMock(return_value=[]))
            monkeypatch.setattr(
                module.repository, "get_due_expirations", AsyncMock(return_value=[])
            )
            worker = module.GhostyTempChannelService(bot).expiry_worker
        elif worker_name == "pause":
            module = import_app("app.services.ghosty_pause_service")
            monkeypatch.setattr(module.repository, "get_all_pauses", query)
            worker = module.GhostyPauseService(bot, None).expiry_worker
        else:
            module = import_app("app.cogs.sticky")
            monkeypatch.setattr(module.repository, "get_stickies", query)
            worker = module.Sticky(bot).reconcile_stickies

        worker.change_interval(seconds=0.01)
        task = worker.start()
        try:
            await asyncio.wait_for(recovered.wait(), timeout=2)
            assert attempts >= 2
            assert worker.is_running()
            assert not worker.failed()
        finally:
            worker.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    asyncio.run(check())
