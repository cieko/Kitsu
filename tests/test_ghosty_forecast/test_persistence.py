from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from .helpers import forecast, prepare


@pytest.mark.parametrize("finish_attempt", [3, 17, 24])
def test_early_hits_and_missed_candidates_retrain_using_actual_outcomes(
    import_app, monkeypatch, finish_attempt
):
    module = import_app("app.services.ghosty_service")
    now = datetime.now(timezone.utc)
    rows = [
        {
            "channel_id": "2",
            "outcome": "hit" if index % 8 == 7 else "miss",
            "result_message_id": str(index + 1),
            "created_at": now - timedelta(days=1) + timedelta(minutes=index),
        }
        for index in range(80)
    ]

    async def check():
        service, documents = prepare(module, monkeypatch)
        documents[1, 2]["streak_forecast"] = forecast()
        service.bot = SimpleNamespace(guilds=[SimpleNamespace(id=1)])
        # Use the real shared predictor and training store, with historical fallback.
        del service._get_prediction_model
        monkeypatch.setattr(module.repository, "get_config", AsyncMock(return_value=False))
        monkeypatch.setattr(
            module.repository,
            "get_prediction_history",
            AsyncMock(side_effect=lambda guild_id: list(rows)),
        )

        async def record(**result):
            stats = documents[1, 2]
            previous = stats["streak"]
            hit = result["outcome"] == "hit"
            stats.update(
                streak=0 if hit else previous + 1,
                total_gambles=stats["total_gambles"] + 1,
                hits=stats["hits"] + int(hit),
            )
            if hit:
                stats.pop("streak_forecast", None)
            rows.append(
                {
                    key: result[key]
                    for key in ("channel_id", "result_message_id", "created_at", "outcome")
                }
            )
            return {**deepcopy(stats), "last_streak": previous}

        monkeypatch.setattr(module.repository, "record_result_atomically", record)
        await service.training_worker()
        assert (await service.get_prediction_data(1, 2))["hit_at"] == 8
        before_candidates = service._live_models[1].predict(2, now=now, current_streak=0)[
            "hit_candidates"
        ]
        probability_before = next(
            candidate["probability"] for candidate in before_candidates if candidate["attempt"] == 8
        )
        fingerprint = service._live_models[1].training_fingerprint
        for attempt in range(1, finish_attempt + 1):
            hit = attempt == finish_attempt
            await service.record_result(
                {
                    "guild_id": 1,
                    "channel_id": 2,
                    "user_id": 7,
                    "source_message_id": 80 + attempt,
                    "result_message_id": 80 + attempt,
                    "wager": 1,
                    "payout": 10 if hit else 0,
                    "outcome": "hit" if hit else "miss",
                    "created_at": now - timedelta(seconds=30 - attempt),
                }
            )
            if attempt == 8:
                await service.training_worker()
                failed = await service.get_prediction_data(1, 2)
                assert failed["hit_at"] == 13 and failed["estimate_status"] == "pending"
                assert service._live_models[1].samples == 88
                assert service._live_models[1].training_fingerprint != fingerprint
                learned = service._live_models[1].predict(2, now=now, current_streak=0)[
                    "hit_candidates"
                ]
                probability_after = next(
                    candidate["probability"] for candidate in learned if candidate["attempt"] == 8
                )
                assert probability_after < probability_before
                dataset = json.loads(
                    (service._training_store.directory / "1/training.json").read_text()
                )
                assert dataset["examples"][-1]["outcome"] == "miss"
                assert dataset["examples"][-1]["result_message_id"] == "88"
            if attempt == 23:
                exhausted = await service.get_prediction_data(1, 2)
                assert exhausted["hit_at"] == 22 and exhausted["estimate_status"] == "missed"
        waiting = service.get_live_prediction_data(1, 2, stats=deepcopy(documents[1, 2]))
        assert not waiting["ready"] and waiting["hit_at"] is None
        assert "streak_forecast" not in waiting
        # A dashboard read must not freeze an estimate from the old weights.
        waiting_dashboard = await service.get_prediction_data(1, 2)
        assert not waiting_dashboard["ready"] and "streak_forecast" not in waiting_dashboard
        assert "streak_forecast" not in documents[1, 2]
        next_streak = await service.get_prediction_data(1, 2, refresh_model=True)
        assert next_streak["hit_at"] == 8 and next_streak["estimate_status"] == "pending"
        assert (1, 2) not in service._pending_forecasts
        assert {candidate["attempt"] for candidate in next_streak["hit_candidates"]} == {
            8,
            finish_attempt,
        }
        assert service._live_models[1].streak_hits[2][finish_attempt] == 1
        trained = json.loads((service._training_store.directory / "1/training.json").read_text())
        assert trained["examples"][-1]["outcome"] == "hit"
        assert trained["examples"][-1]["result_message_id"] == str(80 + finish_attempt)

    asyncio.run(check())


@pytest.mark.parametrize("existing", [False, True])
def test_repository_claim_is_atomic_scoped_and_does_not_create_channels(
    import_app, monkeypatch, existing
):
    module = import_app("app.database.repositories.ghosty_repository")
    collection = MagicMock()
    winner = forecast(target=6)
    collection.find_one_and_update.return_value = {"streak_forecast": winner} if existing else None
    monkeypatch.setattr(module.state, "channel_stats", collection)
    candidate = forecast()
    saved = asyncio.run(module.claim_streak_forecast(1, 2, candidate))
    assert saved == (winner if existing else None)
    query, pipeline = collection.find_one_and_update.call_args.args
    assert query["guild_id"] == "1" and query["channel_id"] == "2"
    assert query["$expr"] == {"$eq": [{"$subtract": ["$total_gambles", "$streak"]}, 80]}
    condition, keep, create = pipeline[0]["$set"]["streak_forecast"]["$cond"]
    assert {"$eq": ["$streak_forecast.streak_base", 80]} in condition["$and"]
    assert {"$eq": ["$streak_forecast.version", 3]} in condition["$and"]
    assert keep == "$streak_forecast" and create == {"$literal": candidate}
    assert not collection.find_one_and_update.call_args.kwargs.get("upsert", False)


def test_missed_target_is_marked_in_tracker_without_moving_the_number(import_app):
    module = import_app("app.cogs.ghosty")

    async def check():
        cog = module.GhostyCog(SimpleNamespace())
        embed = cog._build_tracker_embed(
            {"stats": {"streak": 9, "streak_loss": 100}},
            {"ready": True, "hit_at": 8, "hit_percent": 7.2, "estimate_status": "missed"},
        )
        assert "**Estimated hit @** `was 8 MISSED`" in embed.description
        assert "**Next-gamble chance** was `7.2%`" in embed.description

    asyncio.run(check())


@pytest.mark.parametrize(
    "streak, target, chance, verb",
    [
        (1, "4", "9%", "is"),
        (2, "4", "9%", "is"),
        (4, "8", "4%", "is"),
        (8, "was 8 MISSED", "4%", "was"),
    ],
)
def test_tracker_shows_only_the_selected_candidate_and_remembers_its_missed_chance(
    import_app,
    streak,
    target,
    chance,
    verb,
):
    module = import_app("app.cogs.ghosty")

    async def check():
        cog = module.GhostyCog(SimpleNamespace())
        saved = forecast()
        saved["candidates"] = [
            {"attempt": 2, "probability": 0.08},
            {"attempt": 4, "probability": 0.09},
            {"attempt": 8, "probability": 0.04},
        ]
        prediction = cog.service._display_streak_forecast(
            {"ready": True, "hit_percent": 9.9}, saved, streak
        )
        embed = cog._build_tracker_embed(
            {"stats": {"streak": streak, "streak_loss": 100}}, prediction
        )
        assert embed.description == (
            f"**Streak: {streak} ・ Total Loss: 100 Goos**\n\n"
            f"**Estimated hit @** `{target}`\n"
            f"**Next-gamble chance** {verb} `{chance}`"
        )
        # Hidden candidates remain available internally; formatting does not delete them.
        assert len(prediction["hit_candidates"]) == 3
        assert prediction["hit_candidates"][0]["attempt"] == 2

    asyncio.run(check())
