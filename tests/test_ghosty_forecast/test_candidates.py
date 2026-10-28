from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from .helpers import forecast, model, prepare


def test_candidates_stay_fixed_and_advance_only_after_their_attempts_miss(import_app, monkeypatch):
    module = import_app("app.services.ghosty_service")

    async def check():
        service, documents = prepare(module, monkeypatch)
        first = await service.get_prediction_data(1, 2)
        assert first["hit_at"] == 8 and first["estimate_status"] == "pending"
        chances = []
        # Retraining can publish a different model; the issued target stays put.
        replacement = model(target=20)
        service._live_models[1] = replacement
        service._get_prediction_model.return_value = replacement
        for streak in range(1, 24):
            documents[1, 2].update(streak=streak, total_gambles=80 + streak)
            snapshot = deepcopy(documents[1, 2])
            live = service.get_live_prediction_data(1, 2, stats=snapshot)
            dashboard = await service.get_prediction_data(1, 2, stats=snapshot)
            expected = 8 if streak < 8 else 13 if streak < 13 else 22
            assert live["hit_at"] == dashboard["hit_at"] == expected
            assert live["estimate_status"] == ("missed" if streak >= 22 else "pending")
            assert live["candidate_percent"] == {8: 40, 13: 30, 22: 20}[expected]
            assert [candidate["attempt"] for candidate in live["hit_candidates"]] == [8, 13, 22]
            chances.append(live["hit_percent"])
        assert chances[-1] < chances[0]
        module.repository.claim_streak_forecast.assert_awaited_once()

        restarted = module.GhostyService(None)
        restarted._live_models[1] = replacement
        restored = restarted.get_live_prediction_data(1, 2, stats=deepcopy(documents[1, 2]))
        assert restored["hit_at"] == 22 and restored["estimate_status"] == "missed"
        assert restored["streak_forecast"]["issued_at"] == first["streak_forecast"]["issued_at"]

    asyncio.run(check())


@pytest.mark.parametrize("boundary", ["hit", "manual_reset"])
def test_new_streak_gets_a_new_target_and_other_channels_stay_independent(
    import_app, monkeypatch, boundary
):
    module = import_app("app.services.ghosty_service")

    async def check():
        service, documents = prepare(module, monkeypatch)
        assert (await service.get_prediction_data(1, 2))["hit_at"] == 8
        replacement = model(target=12)
        service._live_models[1] = replacement
        service._get_prediction_model.return_value = replacement
        documents[1, 3] = {"streak": 0, "total_gambles": 160, "hits": 10}
        assert (await service.get_prediction_data(1, 3))["hit_at"] == 12
        assert (await service.get_prediction_data(1, 2))["hit_at"] == 8

        documents[1, 2].update(streak=0, total_gambles=89 if boundary == "hit" else 88)
        if boundary == "hit":
            documents[1, 2].pop("streak_forecast")
        # A manual reset may leave old metadata. The changed run base invalidates it.
        new = await service.get_prediction_data(1, 2)
        assert new["hit_at"] == 12 and new["estimate_status"] == "pending"
        assert new["streak_forecast"]["streak_base"] != 80

    asyncio.run(check())


def test_concurrent_live_and_dashboard_predictions_save_only_one_target(import_app, monkeypatch):
    module = import_app("app.services.ghosty_service")

    async def check():
        service, documents = prepare(module, monkeypatch)
        snapshot = deepcopy(documents[1, 2])
        live = service.get_live_prediction_data(1, 2, stats=snapshot)
        results = await asyncio.gather(
            service.save_streak_prediction(1, 2, stats=snapshot, prediction=live),
            *(service.get_prediction_data(1, 2, stats=snapshot) for _ in range(4)),
        )
        assert {prediction["hit_at"] for prediction in results} == {8}
        module.repository.claim_streak_forecast.assert_awaited_once()

    asyncio.run(check())


def test_an_existing_atomic_winner_overrides_a_different_local_candidate(import_app, monkeypatch):
    module = import_app("app.services.ghosty_service")

    async def check():
        service, documents = prepare(module, monkeypatch)
        stale = deepcopy(documents[1, 2])
        documents[1, 2]["streak_forecast"] = forecast(target=6)
        candidate = service.get_live_prediction_data(1, 2, stats=stale)
        assert candidate["hit_at"] == 8
        saved = await service.save_streak_prediction(1, 2, stats=stale, prediction=candidate)
        assert saved["hit_at"] == 6
        assert service.get_live_prediction_data(1, 2, stats=stale)["hit_at"] == 6

    asyncio.run(check())


def test_mid_run_adoption_does_not_retroactively_claim_a_missed_forecast(import_app, monkeypatch):
    module = import_app("app.services.ghosty_service")

    async def check():
        service, documents = prepare(module, monkeypatch)
        documents[1, 2].update(streak=12, total_gambles=92)
        prediction = await service.get_prediction_data(1, 2)
        assert prediction["hit_at"] == 20 and prediction["estimate_status"] == "pending"
        documents[1, 2].update(streak=13, total_gambles=93)
        assert (await service.get_prediction_data(1, 2))["hit_at"] == 20

    asyncio.run(check())


def test_primary_candidate_is_the_most_likely_remaining_outcome_not_the_earliest(
    import_app, monkeypatch
):
    module = import_app("app.services.ghosty_service")

    async def check():
        service, documents = prepare(module, monkeypatch)
        saved = forecast()
        saved["candidates"] = [
            {"attempt": 8, "probability": 0.2},
            {"attempt": 13, "probability": 0.5},
            {"attempt": 22, "probability": 0.1},
        ]
        documents[1, 2]["streak_forecast"] = saved
        first = await service.get_prediction_data(1, 2)
        assert first["hit_at"] == 13 and first["candidate_percent"] == 50
        documents[1, 2].update(streak=8, total_gambles=88)
        assert (await service.get_prediction_data(1, 2))["hit_at"] == 13
        documents[1, 2].update(streak=13, total_gambles=93)
        advanced = await service.get_prediction_data(1, 2)
        assert advanced["hit_at"] == 22 and advanced["candidate_percent"] == 10
        module.repository.claim_streak_forecast.assert_not_awaited()

    asyncio.run(check())


def test_legacy_single_target_is_migrated_once_to_a_saved_candidate_set(import_app, monkeypatch):
    module = import_app("app.services.ghosty_service")

    async def check():
        service, documents = prepare(module, monkeypatch)
        documents[1, 2]["streak_forecast"] = {"streak_base": 80, "hit_at": 8}
        migrated = await service.get_prediction_data(1, 2)
        assert [candidate["attempt"] for candidate in migrated["hit_candidates"]] == [8, 13, 22]
        assert documents[1, 2]["streak_forecast"]["version"] == 3
        await service.get_prediction_data(1, 2)
        module.repository.claim_streak_forecast.assert_awaited_once()

    asyncio.run(check())


def test_old_geometric_forecast_is_replaced_once_by_highest_probability_history_outcome(
    import_app, monkeypatch
):
    module = import_app("app.services.ghosty_service")
    from app.services.ghosty_prediction import GhostyPredictor

    now = datetime.now(timezone.utc)
    outcomes = ["hit"]
    for length in [8] * 6 + [13] * 20 + [22] * 10:
        outcomes.extend(["miss"] * (length - 1) + ["hit"])
    rows = [
        {
            "channel_id": "2",
            "outcome": outcome,
            "result_message_id": str(index + 1),
            "created_at": now - timedelta(days=1) + timedelta(seconds=index),
        }
        for index, outcome in enumerate(outcomes)
    ]
    predictor = GhostyPredictor(rows, now=now, enabled=False)

    async def check():
        service, documents = prepare(module, monkeypatch)
        stats = documents[1, 2]
        stats.update(total_gambles=len(rows), hits=outcomes.count("hit"))
        stats["streak_forecast"] = {
            "version": 2,
            "streak_base": len(rows),
            "candidates": [{"attempt": attempt, "probability": 0.1} for attempt in (1, 2, 3)],
        }
        service._live_models[1] = predictor
        service._get_prediction_model.return_value = predictor

        migrated = await service.get_prediction_data(1, 2)
        assert migrated["hit_at"] == 13
        assert migrated["candidate_percent"] == max(
            candidate["probability"] * 100 for candidate in migrated["hit_candidates"]
        )
        assert {candidate["attempt"] for candidate in migrated["hit_candidates"]} == {8, 13, 22}
        assert stats["streak_forecast"]["version"] == 3

        stats.update(streak=13, total_gambles=len(rows) + 13)
        advanced = await service.get_prediction_data(1, 2)
        assert advanced["hit_at"] == 22
        module.repository.claim_streak_forecast.assert_awaited_once()

    asyncio.run(check())


def test_persistence_failure_is_retried_without_replacing_the_chosen_target(
    import_app, monkeypatch
):
    module = import_app("app.services.ghosty_service")

    async def check():
        service, documents = prepare(module, monkeypatch)
        claim = module.repository.claim_streak_forecast.side_effect
        module.repository.claim_streak_forecast.side_effect = RuntimeError("database unavailable")
        with pytest.raises(RuntimeError):
            await service.get_prediction_data(1, 2)
        service._get_prediction_model.return_value = model(target=20)
        module.repository.claim_streak_forecast.side_effect = claim
        recovered = await service.get_prediction_data(1, 2)
        assert recovered["hit_at"] == 8
        assert documents[1, 2]["streak_forecast"]["hit_at"] == 8

    asyncio.run(check())
