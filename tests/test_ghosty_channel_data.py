import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


def test_stats_reads_return_zero_without_populating_the_database(import_app, monkeypatch):
    module = import_app("app.services.ghosty_service")
    collection = MagicMock()
    collection.find_one.return_value = None
    monkeypatch.setattr(module.repository.state, "channel_stats", collection)

    async def check():
        service = module.GhostyService(None)
        for _ in range(2):
            stats = await service.get_stats(1, 2)
            assert stats == {
                "streak": 0,
                "streak_loss": 0,
                "net_pl": 0,
                "hits": 0,
                "misses": 0,
                "total_gambles": 0,
            }

    asyncio.run(check())
    assert collection.find_one.call_count == 2
    collection.find_one.assert_called_with({"guild_id": "1", "channel_id": "2"})
    collection.update_one.assert_not_called()
    collection.insert_one.assert_not_called()


def test_dashboard_repository_reads_only_channels_with_recorded_activity(import_app, monkeypatch):
    module = import_app("app.database.repositories.ghosty_repository")
    collection = MagicMock()
    collection.find.return_value.__iter__.return_value = iter([])
    monkeypatch.setattr(module.state, "channel_stats", collection)
    assert asyncio.run(module.get_all_channel_stats(1)) == []
    collection.find.assert_called_once_with({"guild_id": "1", "total_gambles": {"$gt": 0}})
    collection.update_one.assert_not_called()


@pytest.mark.parametrize("outcome", ["hit", "miss"])
@pytest.mark.parametrize("user_id", [0, 7])
def test_recording_writes_history_and_channel_stats_without_user_snapshots(
    import_app,
    monkeypatch,
    outcome,
    user_id,
):
    module = import_app("app.database.repositories.ghosty_repository")
    client, history, stats, users = (MagicMock() for _ in range(4))
    session = client.start_session.return_value.__enter__.return_value
    session.with_transaction.side_effect = lambda callback: callback(session)
    stats.find_one.side_effect = [
        {"streak": 5, "streak_loss": 50},
        {"streak": 0 if outcome == "hit" else 6},
    ]
    monkeypatch.setattr(module.state, "client", client)
    monkeypatch.setattr(module.state, "gamble_history", history)
    monkeypatch.setattr(module.state, "channel_stats", stats)
    monkeypatch.setattr(module, "user_entries", users, raising=False)
    stamp = datetime(2026, 10, 27, tzinfo=timezone.utc)
    module._record_result_transaction(
        guild_id=1,
        channel_id=2,
        user_id=user_id,
        source_message_id=3,
        result_message_id=4,
        command_message_id=None,
        wager=10,
        outcome=outcome,
        payout=100 if outcome == "hit" else 0,
        source="live",
        created_at=stamp,
    )
    history.insert_one.assert_called_once()
    document = history.insert_one.call_args.args[0]
    assert document["channel_id"] == "2" and document["created_at"] == stamp
    assert document["user_id"] == str(user_id)
    stats.update_one.assert_called_once()
    identity, update = stats.update_one.call_args.args
    assert identity == {"guild_id": "1", "channel_id": "2"}
    assert update["$set"]["streak"] == (0 if outcome == "hit" else 6)
    assert update["$inc"]["total_gambles"] == 1
    if outcome == "hit":
        assert update["$unset"] == {"tracker_message_id": "", "streak_forecast": ""}
    else:
        assert "$unset" not in update
    assert not users.mock_calls


@pytest.mark.parametrize("existing", [False, True])
def test_tracker_pointer_only_updates_an_existing_channel(import_app, monkeypatch, existing):
    module = import_app("app.database.repositories.ghosty_repository")
    collection = MagicMock()
    collection.update_one.return_value.matched_count = int(existing)
    monkeypatch.setattr(module.state, "channel_stats", collection)
    if existing:
        asyncio.run(module.set_tracker_message(1, 2, 3))
    else:
        with pytest.raises(RuntimeError):
            asyncio.run(module.set_tracker_message(1, 2, 3))
    collection.update_one.assert_called_once_with(
        {"guild_id": "1", "channel_id": "2"},
        {"$set": {"tracker_message_id": "3"}},
    )


def test_last_gamble_lookup_is_channel_scoped_and_ignores_future_records(import_app, monkeypatch):
    module = import_app("app.database.repositories.ghosty_repository")
    collection = MagicMock()
    stamp = datetime(2026, 10, 27, tzinfo=timezone.utc)
    collection.find_one.return_value = {"created_at": stamp}
    monkeypatch.setattr(module.state, "gamble_history", collection)
    assert asyncio.run(module.get_last_gamble_at(1, 2, before=stamp)) == stamp
    query, projection = collection.find_one.call_args.args
    assert query == {
        "guild_id": "1",
        "channel_id": "2",
        "outcome": {"$in": ["hit", "miss"]},
        "created_at": {"$lte": stamp},
    }
    assert projection == {"_id": 0, "created_at": 1}
    collection.update_one.assert_not_called()


def test_inactive_channel_keeps_its_last_gamble_outside_training_window(import_app, monkeypatch):
    module = import_app("app.services.ghosty_service")
    stamp = datetime(2026, 10, 1, tzinfo=timezone.utc)
    model = SimpleNamespace(
        samples=20_000, contexts={}, predict=MagicMock(return_value={"ready": True})
    )
    monkeypatch.setattr(
        module.repository, "get_channel_stats", AsyncMock(return_value={"streak": 3})
    )
    monkeypatch.setattr(
        module.repository, "get_gamble_counts", AsyncMock(return_value={"total": 100, "hits": 10})
    )
    monkeypatch.setattr(module.repository, "get_last_gamble_at", AsyncMock(return_value=stamp))

    async def check():
        service = module.GhostyService(None)
        service._get_prediction_model = AsyncMock(return_value=model)
        assert (await service.get_prediction_data(1, 2))["ready"]

    asyncio.run(check())
    module.repository.get_last_gamble_at.assert_awaited_once()
    assert module.repository.get_last_gamble_at.call_args.args == (1, 2)
    assert model.predict.call_args.kwargs["last_gamble_at"] == stamp
    assert model.predict.call_args.kwargs["current_streak"] == 3
