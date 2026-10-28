import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.ghosty_prediction import GhostyPredictor


NOW = datetime.now(timezone.utc).replace(microsecond=0)


def history():
    return [{"channel_id": "2", "created_at": NOW - timedelta(days=2) + timedelta(minutes=index),
             "result_message_id": str(index + 1), "outcome": "hit" if index < 10 else "miss"}
            for index in range(100)]


def mock_recording(module, monkeypatch):
    monkeypatch.setattr(module.repository, "get_config", AsyncMock(return_value=True))
    monkeypatch.setattr(module.repository, "record_result_atomically", AsyncMock(return_value={
        "streak": 3, "last_streak": 2, "hits": 10, "total_gambles": 101,
    }))
    monkeypatch.setattr(module.repository, "get_gamble_by_result_message", AsyncMock(
        side_effect=AssertionError("The service must not repeat the repository's deduplication query"),
    ))


@pytest.mark.parametrize("case", ["mention", "named_player", "cached_command", "history_fallback", "unrelated", "duplicate"])
def test_processing_avoids_history_and_redundant_queries_when_the_player_is_known(import_app, monkeypatch, case):
    module = import_app("app.services.ghosty_service")
    mock_recording(module, monkeypatch)
    member = SimpleNamespace(id=7, bot=False, name="player7", display_name="player7")
    guild = SimpleNamespace(id=1, chunked=True, members=[member])
    channel = SimpleNamespace(id=2)
    command = SimpleNamespace(guild=guild, channel=channel, author=member, id=900,
                              content="?give ghosty 100", created_at=NOW - timedelta(seconds=1))

    async def preceding(**kwargs):
        yield command

    channel.history = MagicMock(side_effect=preceding)
    contents = {"mention": "<@7>, you've successfully sent 100 goos",
                "named_player": "**player7 <:badge:12345>**, you've successfully sent 100 goos",
                "cached_command": "You've successfully sent 100 goos", "history_fallback": "You've successfully sent 100 goos",
                "unrelated": "Your balance is 100 goos", "duplicate": "<@7>, you've successfully sent 100 goos"}
    result = SimpleNamespace(id=1000, guild=guild, channel=channel, author=SimpleNamespace(id=module.GHOSTY_BOT_ID),
                             created_at=NOW, content=contents[case], reference=None)
    if case == "duplicate":
        module.repository.record_result_atomically.return_value = None
    bot = SimpleNamespace(cached_messages=[command] if case in {"cached_command", "named_player"} else [])
    if case == "named_player":
        command.author = SimpleNamespace(id=8, bot=False)

    async def check():
        service = module.GhostyService(bot)
        recorded = await service.process_message(result)
        if case == "unrelated":
            assert recorded is None
            module.repository.get_config.assert_not_awaited()
            module.repository.record_result_atomically.assert_not_awaited()
        else:
            module.repository.record_result_atomically.assert_awaited_once()
            assert module.repository.record_result_atomically.call_args.kwargs["user_id"] == 7
            if case == "duplicate":
                assert recorded is None and not service._recent_results
            else:
                assert recorded["stats"]["streak"] == 3
        assert channel.history.call_count == int(case == "history_fallback")
        module.repository.get_gamble_by_result_message.assert_not_awaited()

    asyncio.run(check())


@pytest.mark.parametrize("warm", [False, True])
def test_live_prediction_uses_current_counts_without_waiting_for_training_or_database(import_app, monkeypatch, warm):
    module = import_app("app.services.ghosty_service")

    async def check():
        service = module.GhostyService(None)
        model = GhostyPredictor(history(), now=NOW)
        original = deepcopy(model.to_state())
        if warm:
            service._live_models[1] = model
        service._get_prediction_model = AsyncMock(side_effect=AssertionError("Do not wait for training"))
        monkeypatch.setattr(module.repository, "get_channel_stats", AsyncMock(side_effect=AssertionError("Do not read MongoDB")))
        monkeypatch.setattr(service._training_store, "load_or_train", MagicMock(side_effect=AssertionError("Do not train")))
        lock = service._prediction_locks.setdefault(1, asyncio.Lock())
        await lock.acquire()
        try:
            event = {"channel_id": 2, "outcome": "miss", "created_at": NOW, "stats": {"streak": 8}}
            service._observe_live_result(1, event)
            prediction = service.get_live_prediction_data(
                1, 2, stats={"streak": 8, "hits": 10, "total_gambles": 101}, last_gamble_at=NOW,
            )
            assert prediction["ready"] and prediction["hit_percent"] == 9.9
            assert prediction["hit_at"] is None and prediction["samples"] == 101
            assert service._live_contexts[1][2].streak == 8
            assert service._live_contexts[1][2].last_gamble_at == NOW
            assert model.to_state() == original
            service._get_prediction_model.assert_not_awaited()
        finally:
            lock.release()

    asyncio.run(check())


def test_training_replays_results_arriving_during_fit_without_double_counting_context(import_app, monkeypatch):
    module = import_app("app.services.ghosty_service")
    mock_recording(module, monkeypatch)
    rows = history()
    entered, release = None, None

    async def check():
        nonlocal entered, release
        entered, release = asyncio.Event(), asyncio.Event()

        async def load(guild_id):
            snapshot = list(rows)
            if not entered.is_set():
                entered.set()
                await release.wait()
            return snapshot

        monkeypatch.setattr(module.repository, "get_prediction_history", load)
        service = module.GhostyService(None)
        loading = asyncio.create_task(service._get_prediction_model(1, refresh=True))
        await entered.wait()
        await service.record_result({"guild_id": 1, "channel_id": 2, "user_id": 7,
                                     "source_message_id": 101, "result_message_id": 101,
                                     "created_at": NOW, "wager": 100, "outcome": "miss", "payout": 0})
        release.set()
        model = await loading
        assert model.samples == 100
        assert service._live_contexts[1][2].streak == 3
        assert service._live_contexts[1][2].session_streak == 1
        assert service._live_contexts[1][2].last_gamble_at == NOW
        assert len(service._recent_results[1]) == 1
        rows.append({"channel_id": "2", "created_at": NOW, "result_message_id": "101", "outcome": "miss"})
        trained = await service._get_prediction_model(1, refresh=True)
        assert trained.samples == 101
        assert not service._recent_results[1] and not service._live_contexts[1]
        assert trained.contexts[2].session_streak == 1
        assert trained.contexts[2].last_gamble_at == NOW

    asyncio.run(check())
