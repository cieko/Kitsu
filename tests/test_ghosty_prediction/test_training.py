from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from app.services.ghosty_prediction import GhostyPredictor

from .helpers import CHANNEL, NOW, history


def test_model_can_learn_short_run_after_long_run_when_it_is_observable():
    outcomes = ["hit"] + (["miss"] * 25 + ["hit"] + ["miss"] * 7 + ["hit"]) * 100
    model = GhostyPredictor(history(outcomes), now=NOW)
    assert model.active and model.mask[7] == 1
    assert model.weights[7] > 0
    model.contexts[CHANNEL].previous_streak = 25
    after_long = model.predict(CHANNEL, now=NOW, current_streak=7)
    model.contexts[CHANNEL].previous_streak = 7
    other = model.predict(CHANNEL, now=NOW, current_streak=7)
    assert after_long["hit_percent"] > other["hit_percent"]


def test_service_reuses_weights_after_results_until_background_training_or_expiry(
    import_app, monkeypatch
):
    module = import_app("app.services.ghosty_service")
    rows = history(["hit"] * 10 + ["miss"] * 90)
    load = AsyncMock(return_value=rows)
    monkeypatch.setattr(module.repository, "get_prediction_history", load)
    monkeypatch.setattr(module.repository, "get_config", AsyncMock(return_value=True))
    monkeypatch.setattr(
        module.repository,
        "get_channel_stats",
        AsyncMock(return_value={"streak": 7, "total_gambles": 100, "hits": 10}),
    )
    monkeypatch.setattr(
        module.repository,
        "claim_streak_forecast",
        AsyncMock(side_effect=lambda guild_id, channel_id, forecast: forecast),
    )
    monkeypatch.setattr(
        module.repository, "record_result_atomically", AsyncMock(return_value={"streak": 8})
    )

    async def check():
        service = module.GhostyService(None)
        predictions = await asyncio.gather(
            *[service.get_prediction_data(1, CHANNEL) for _ in range(5)]
        )
        load.assert_awaited_once_with(1)
        # This fixture only observed hits at attempt 1, already passed here.
        # Weight reuse must not manufacture a new future target.
        assert all(prediction["hit_at"] is None for prediction in predictions)
        await service.get_prediction_data(2, CHANNEL)
        assert load.await_count == 2
        result = {
            "guild_id": 1,
            "channel_id": CHANNEL,
            "user_id": 5,
            "source_message_id": 50,
            "result_message_id": 51,
            "wager": 100,
            "outcome": "miss",
            "payout": 0,
        }
        await service.record_result(result)
        assert 1 in service._prediction_cache
        assert 2 in service._prediction_cache
        await service.get_prediction_data(1, CHANNEL)
        assert load.await_count == 2
        service.invalidate_prediction_cache(1)
        assert 1 not in service._prediction_cache
        await service.get_prediction_data(1, CHANNEL)
        assert load.await_count == 3
        service._prediction_cache[1] = (0, service._prediction_cache[1][1])
        await service.get_prediction_data(1, CHANNEL)
        assert load.await_count == 4

    asyncio.run(check())


def test_result_arriving_during_training_does_not_cache_stale_snapshot(import_app, monkeypatch):
    module = import_app("app.services.ghosty_service")
    rows = history(["miss", "hit"] * 50)
    monkeypatch.setattr(module.repository, "get_config", AsyncMock(return_value=True))
    monkeypatch.setattr(
        module.repository, "get_channel_stats", AsyncMock(return_value={"streak": 0})
    )

    async def check():
        service = module.GhostyService(None)
        calls = 0

        async def load(guild_id):
            nonlocal calls
            calls += 1
            if calls == 1:
                service.invalidate_prediction_cache(guild_id)
            return rows

        monkeypatch.setattr(module.repository, "get_prediction_history", load)
        await service.get_prediction_data(1, CHANNEL)
        assert 1 not in service._prediction_cache
        await service.get_prediction_data(1, CHANNEL)
        assert 1 in service._prediction_cache
        assert calls == 2

    asyncio.run(check())


def test_prediction_history_is_guild_scoped_projected_and_bounded(import_app, monkeypatch):
    module = import_app("app.database.repositories.ghosty_repository")
    collection = MagicMock()
    cursor = collection.find.return_value
    cursor.sort.return_value = cursor
    cursor.limit.return_value = cursor
    cursor.__iter__.return_value = iter([])
    monkeypatch.setattr(module.state, "gamble_history", collection)
    assert asyncio.run(module.get_prediction_history(7)) == []
    query, projection = collection.find.call_args.args
    assert query == {"guild_id": "7"}
    assert "user_id" not in projection
    cursor.limit.assert_called_once_with(20_000)


@pytest.mark.parametrize("authorized", [False, True])
def test_prediction_config_requires_manager_and_preserves_threshold(
    import_app,
    monkeypatch,
    authorized,
):
    module = import_app("app.cogs.ghosty")
    config = {"minimum_samples": 75}
    save = AsyncMock(side_effect=lambda guild_id, key, value: config.__setitem__(key, value))
    monkeypatch.setattr(module.repository, "set_config", save)
    monkeypatch.setattr(
        module.repository,
        "get_config",
        AsyncMock(
            side_effect=lambda guild_id, key, default=None: config.get(key, default),
        ),
    )

    async def check():
        cog = module.GhostyCog(SimpleNamespace())
        current = SimpleNamespace(
            guild=SimpleNamespace(id=1),
            user=SimpleNamespace(guild_permissions=discord.Permissions(manage_guild=authorized)),
            response=SimpleNamespace(send_message=AsyncMock()),
        )
        await cog.configure.callback(cog, current, pattern_predictions=False)
        assert config["minimum_samples"] == 75
        assert save.await_count == int(authorized)
        if authorized:
            assert config["prediction_patterns"] is False
            assert "historical rates only" in current.response.send_message.call_args.args[0]

    asyncio.run(check())
