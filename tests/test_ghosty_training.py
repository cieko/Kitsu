import asyncio
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from pymongo.errors import AutoReconnect

from app.services.ghosty_prediction import GhostyPredictor
from app.services.ghosty_training import GhostyTrainingStore


BASE = datetime(2026, 10, 1, tzinfo=timezone.utc)
NOW = BASE + timedelta(days=26)
CHANNEL = (int((BASE - timedelta(days=30)).timestamp() * 1000) - 1420070400000) << 22


def history(count=100):
    return [
        {"channel_id": str(CHANNEL), "created_at": BASE + timedelta(minutes=i),
         "result_message_id": str(i + 1), "outcome": "hit" if i % 10 == 0 else "miss",
         "user_id": "private", "wager": 100, "message_content": "not needed"}
        for i in range(count)
    ]


def test_saves_projected_dataset_and_matching_model(tmp_path):
    store = GhostyTrainingStore(tmp_path)
    model = store.load_or_train(1, history(), now=NOW, enabled=True)
    dataset = json.loads((tmp_path / "1/training.json").read_text(encoding="utf-8"))
    saved = json.loads((tmp_path / "1/model.json").read_text(encoding="utf-8"))
    assert dataset["samples"] == 100
    assert dataset["guild_id"] == saved["guild_id"] == "1"
    assert set(dataset["examples"][0]) == {
        "channel_id", "result_message_id", "created_at", "outcome",
    }
    assert saved["model"]["training_fingerprint"] == dataset["dataset_sha256"]
    assert saved["model"]["weights"] == model.weights.tolist()
    assert saved["model"]["validation"] == model.validation


def test_unchanged_data_reuses_weights_in_memory_and_after_restart(tmp_path):
    store = GhostyTrainingStore(tmp_path)
    model = store.load_or_train(1, history(), now=NOW, enabled=True)
    paths = [tmp_path / "1/training.json", tmp_path / "1/model.json"]
    timestamps = [path.stat().st_mtime_ns for path in paths]
    with patch.object(GhostyPredictor, "__init__", side_effect=AssertionError("Unexpected retraining")):
        current = store.load_or_train(1, list(reversed(history())), now=NOW, enabled=True, current=model)
        restored = GhostyTrainingStore(tmp_path).load_or_train(
            1, history(), now=NOW + timedelta(hours=1), enabled=True,
        )
    assert current is model
    assert restored.to_state() == model.to_state()
    assert [path.stat().st_mtime_ns for path in paths] == timestamps


def test_new_results_retrain_and_update_saved_dataset(tmp_path):
    store = GhostyTrainingStore(tmp_path)
    old = store.load_or_train(1, history(), now=NOW, enabled=True)
    new = store.load_or_train(1, history(101), now=NOW + timedelta(minutes=1), enabled=True, current=old)
    assert new is not old
    assert new.samples == 101
    assert new.training_fingerprint != old.training_fingerprint
    assert new.trained_at > old.trained_at
    assert json.loads((tmp_path / "1/model.json").read_text())["model"]["samples"] == 101
    assert json.loads((tmp_path / "1/training.json").read_text())["samples"] == 101


def test_changed_pattern_setting_rebuilds_model(tmp_path):
    store = GhostyTrainingStore(tmp_path)
    old = store.load_or_train(1, history(), now=NOW, enabled=True)
    new = store.load_or_train(1, history(), now=NOW, enabled=False, current=old)
    assert new is not old
    assert new.training_fingerprint == old.training_fingerprint
    assert new.validation["status"] == "disabled"
    assert not new.patterns_enabled


@pytest.mark.parametrize("damage", ["broken_json", "old_version", "bad_weights"])
def test_invalid_model_is_rebuilt_from_authoritative_history(tmp_path, damage):
    store = GhostyTrainingStore(tmp_path)
    old = store.load_or_train(1, history(), now=NOW, enabled=True)
    path = tmp_path / "1/model.json"
    payload = json.loads(path.read_text())
    if damage == "broken_json":
        path.write_text("{broken", encoding="utf-8")
    else:
        if damage == "old_version":
            payload["model"]["version"] = 1
        else:
            payload["model"]["weights"] = [0]
        path.write_text(json.dumps(payload), encoding="utf-8")
    recovered = store.load_or_train(1, history(), now=NOW + timedelta(minutes=1), enabled=True)
    assert recovered.samples == old.samples
    assert recovered.trained_at > old.trained_at
    restored = GhostyPredictor.from_state(json.loads(path.read_text())["model"])
    assert restored.to_state() == recovered.to_state()


def test_disk_failure_keeps_predictions_available_and_save_is_retried(tmp_path, monkeypatch):
    store = GhostyTrainingStore(tmp_path)
    write = store._write
    monkeypatch.setattr(store, "_write", lambda *args: (_ for _ in ()).throw(OSError("disk full")))
    model = store.load_or_train(1, history(), now=NOW, enabled=True)
    assert model.predict(CHANNEL, now=NOW, current_streak=0)["ready"]
    monkeypatch.setattr(store, "_write", write)
    saved = store.load_or_train(1, history(), now=NOW, enabled=True, current=model)
    assert saved is model
    assert (tmp_path / "1/model.json").is_file()


def test_failed_atomic_replace_preserves_previous_complete_model(tmp_path, monkeypatch):
    store = GhostyTrainingStore(tmp_path)
    old = store.load_or_train(1, history(), now=NOW, enabled=True)
    model_path = tmp_path / "1/model.json"
    original = model_path.read_text()
    replace = type(model_path).replace

    def fail_model_replace(path, target):
        if target.name == "model.json":
            raise OSError("replace interrupted")
        return replace(path, target)

    monkeypatch.setattr(type(model_path), "replace", fail_model_replace)
    current = store.load_or_train(1, history(101), now=NOW, enabled=True, current=old)
    assert current.samples == 101
    assert model_path.read_text() == original
    assert not list(model_path.parent.glob("*.tmp"))
    monkeypatch.setattr(type(model_path), "replace", replace)
    repaired = store.load_or_train(1, history(101), now=NOW, enabled=True, current=current)
    assert repaired is current
    assert json.loads(model_path.read_text())["model"]["samples"] == 101


def test_background_worker_retrains_after_result_without_dashboard_request(import_app, monkeypatch):
    module = import_app("app.services.ghosty_service")
    rows = history()
    monkeypatch.setattr(module.repository, "get_config", AsyncMock(return_value=True))
    monkeypatch.setattr(module.repository, "get_prediction_history", AsyncMock(side_effect=lambda guild_id: rows))
    monkeypatch.setattr(module.repository, "record_result_atomically", AsyncMock(return_value={"streak": 1}))

    async def check():
        service = module.GhostyService(SimpleNamespace(guilds=[SimpleNamespace(id=1)]))
        assert service.training_worker.seconds == 60
        await service.training_worker()
        old = service._prediction_cache[1][1]
        rows[:] = history(101)
        await service.record_result({
            "guild_id": 1, "channel_id": CHANNEL, "user_id": 7,
            "source_message_id": 101, "result_message_id": 101,
            "wager": 100, "outcome": "hit", "payout": 1000,
        })
        await service.training_worker()
        current = service._prediction_cache[1][1]
        assert current.samples == 101
        assert current.training_fingerprint != old.training_fingerprint
        path = service._training_store.directory / "1/model.json"
        assert json.loads(path.read_text())["model"]["samples"] == 101

    asyncio.run(check())


def test_background_worker_starts_stops_and_recovers_database_failure(import_app, monkeypatch):
    module = import_app("app.services.ghosty_service")
    monkeypatch.setattr(module.repository, "get_config", AsyncMock(return_value=True))

    async def check():
        loop = asyncio.get_running_loop()
        saved = asyncio.Event()
        attempts = 0

        async def load(guild_id):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise AutoReconnect("temporary database outage")
            return history()

        monkeypatch.setattr(module.repository, "get_prediction_history", load)
        bot = SimpleNamespace(guilds=[SimpleNamespace(id=1)], wait_until_ready=AsyncMock())
        service = module.GhostyService(bot)
        service.initialize = AsyncMock()
        original = service._training_store.load_or_train

        def persist(*args, **kwargs):
            model = original(*args, **kwargs)
            loop.call_soon_threadsafe(saved.set)
            return model

        monkeypatch.setattr(service._training_store, "load_or_train", persist)
        service.training_worker.change_interval(seconds=0.01)
        await service.start()
        task = service.training_worker.get_task()
        try:
            await asyncio.wait_for(saved.wait(), timeout=3)
            assert attempts >= 2
            assert service.training_worker.is_running()
            bot.wait_until_ready.assert_awaited_once()
        finally:
            service.stop()
            try:
                await task
            except asyncio.CancelledError:
                pass
        assert not service.training_worker.is_running()

    asyncio.run(check())
