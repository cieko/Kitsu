from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from app.services.ghosty_prediction import (
    FEATURES,
    IST,
    Context,
    GhostyPredictor,
    channel_age_hours,
    feature_vector,
    session_day,
)

from .helpers import BASE, CHANNEL, NOW, history, snowflake


def test_reset_boundary_uses_0530_ist_and_preserves_overall_streak():
    before = datetime(2026, 10, 27, 5, 29, 59, tzinfo=IST)
    after = before + timedelta(seconds=1)
    assert session_day(before).isoformat() == "2026-10-26"
    assert session_day(after).isoformat() == "2026-10-27"
    assert session_day(after.astimezone(timezone.utc).replace(tzinfo=None)) == session_day(after)
    context = Context(streak=12, session_streak=7, day=session_day(before))
    context.roll_day(after)
    assert context.session_streak == 0
    assert context.streak == 12


def test_channel_age_comes_from_discord_creation_time():
    channel = snowflake(NOW - timedelta(hours=1))
    assert channel_age_hours(channel, NOW) == 1
    assert feature_vector(Context(), channel, NOW)[5] == 1
    assert feature_vector(Context(), channel, NOW + timedelta(hours=23))[5] == 0


def test_creation_and_last_gamble_use_the_same_channel_reset_boundary():
    before = datetime(2026, 10, 27, 5, 29, 59, tzinfo=IST)
    after = before + timedelta(seconds=1)
    channel = snowflake(before)
    context = Context()
    context.roll_day(before)
    context.observe(False, at=before)
    context.roll_day(after)
    features = dict(zip(FEATURES, feature_vector(context, channel, after)))
    assert features["created_since_reset"] == 0
    assert features["has_previous_gamble"] == features["last_gamble_before_reset"] == 1
    assert features["gamble_gap"] > 0
    assert context.last_gamble_at == before.astimezone(timezone.utc)
    assert context.session_streak == 0 and context.streak == 1

    fresh = snowflake(after)
    assert dict(zip(FEATURES, feature_vector(Context(), fresh, after)))["created_since_reset"] == 1
    for missing in (Context(), Context(last_gamble_at=after + timedelta(seconds=1))):
        features = dict(zip(FEATURES, feature_vector(missing, fresh, after)))
        assert features["has_previous_gamble"] == features["gamble_gap"] == 0
        assert features["last_gamble_before_reset"] == 0


def test_interleaved_training_keeps_timing_per_channel_without_label_leakage(monkeypatch):
    import app.services.ghosty_prediction as module

    first = datetime(2026, 10, 27, 5, 10, tzinfo=IST)
    other = snowflake(first + timedelta(minutes=21))
    events = [
        (CHANNEL, first),
        (other, first + timedelta(minutes=25)),
        (CHANNEL, first + timedelta(minutes=30)),
        (other, first + timedelta(minutes=45)),
    ]
    vectors = []
    original = module.feature_vector

    def capture(context, channel_id, now):
        vector = original(context, channel_id, now)
        vectors.append(dict(zip(FEATURES, vector)))
        return vector

    monkeypatch.setattr(module.model, "feature_vector", capture)
    rows = [
        {
            "channel_id": str(channel),
            "created_at": stamp,
            "result_message_id": str(i + 1),
            "outcome": "miss",
        }
        for i, (channel, stamp) in enumerate(events)
    ]
    model = GhostyPredictor(rows, now=events[-1][1])
    assert vectors[0]["has_previous_gamble"] == vectors[1]["has_previous_gamble"] == 0
    assert vectors[2]["last_gamble_before_reset"] == 1
    assert vectors[3]["last_gamble_before_reset"] == 0
    assert vectors[2]["gamble_gap"] > vectors[3]["gamble_gap"] > 0
    assert vectors[2]["created_since_reset"] == 0
    assert vectors[3]["created_since_reset"] == 1
    assert model.contexts[CHANNEL].last_gamble_at == events[2][1]
    assert model.contexts[other].last_gamble_at == events[3][1]
    restored = GhostyPredictor.from_state(json.loads(json.dumps(model.to_state())))
    assert restored.contexts == model.contexts


def test_model_learns_last_gamble_gap_and_updates_time_without_retraining():
    random = np.random.default_rng(73)
    stamp = BASE
    rows = []
    for i in range(1200):
        gap = 6 if random.random() < 0.5 else 1 / 60
        stamp += timedelta(hours=gap)
        rows.append(
            {
                "channel_id": str(CHANNEL),
                "created_at": stamp,
                "result_message_id": str(i + 1),
                "outcome": "hit" if random.random() < (0.7 if gap == 6 else 0.03) else "miss",
            }
        )
    model = GhostyPredictor(rows, now=stamp)
    assert model.active
    column = FEATURES.index("gamble_gap")
    assert model.mask[column] == 1 and model.weights[column] > 0
    last = model.contexts[CHANNEL].last_gamble_at
    recent = model.predict(CHANNEL, now=stamp + timedelta(minutes=1), current_streak=0)
    idle = model.predict(CHANNEL, now=stamp + timedelta(hours=6), current_streak=0)
    assert idle["hit_percent"] > recent["hit_percent"]
    assert model.contexts[CHANNEL].last_gamble_at == last


def test_partial_first_streak_is_not_evidence_of_a_previous_long_run():
    context = Context()
    for _ in range(30):
        context.observe(False)
    context.observe(True)
    assert context.previous_streak is None
    for _ in range(20):
        context.observe(False)
    context.observe(True)
    assert context.previous_streak == 20
    for misses in (4, 14):
        context.streak = misses
        assert feature_vector(context, CHANNEL, NOW)[7] == 1
    context.streak = 15
    assert feature_vector(context, CHANNEL, NOW)[7] == 0


@pytest.mark.parametrize("hits", [0, 10, 100])
def test_historical_fallback_preserves_rates_and_expected_attempt(hits):
    model = GhostyPredictor(history(["hit"] * hits + ["miss"] * (100 - hits)), now=NOW)
    prediction = model.predict(CHANNEL, now=NOW, current_streak=7)
    assert prediction["ready"]
    assert prediction["model"] == "historical"
    assert prediction["hit_percent"] == hits
    if hits == 0:
        assert prediction["hit_at"] is None
        assert prediction["hit_interval"] is None
    else:
        assert prediction["hit_at"] == 7 + round(100 / hits)
        expected = tuple(
            8 if hits == 100 else 7 + math.ceil(math.log(1 - q) / math.log(1 - hits / 100))
            for q in (0.25, 0.75)
        )
        assert prediction["hit_interval"] == expected


def test_future_results_cannot_influence_prediction_and_order_is_deterministic():
    rows = history(["miss", "hit"] * 50)
    expected = GhostyPredictor(rows, now=NOW).predict(CHANNEL, now=NOW, current_streak=0)
    future = {**rows[-1], "created_at": NOW + timedelta(days=1), "outcome": "hit"}
    result = GhostyPredictor([future, *reversed(rows)], now=NOW)
    assert result.samples == 100
    assert result.predict(CHANNEL, now=NOW, current_streak=0) == expected


def test_current_counter_reset_does_not_reuse_cached_historical_streak():
    model = GhostyPredictor(history(["hit"] * 10 + ["miss"] * 90), now=NOW)
    assert model.contexts[CHANNEL].streak == 90
    prediction = model.predict(CHANNEL, now=NOW, current_streak=0)
    assert prediction["hit_at"] == 10
    assert prediction["hit_percent"] == 10
    assert model.contexts[CHANNEL].streak == 90


def test_insufficient_samples_and_unverified_new_channel_do_not_get_a_boost():
    model = GhostyPredictor(history(["hit"] * 10 + ["miss"] * 90), now=NOW)
    channel = snowflake(NOW - timedelta(hours=1))
    prediction = model.predict(channel, now=NOW, current_streak=0)
    assert not prediction["ready"]
    assert prediction["hit_percent"] is None
    assert not prediction["pooled"]


def test_model_learns_observable_new_channel_pattern(new_channel_model):
    model = new_channel_model
    assert model.active
    assert model.mask[5] == 1
    assert model.weights[5] > 0
    assert model.validation["model_log_loss"] < model.validation["baseline_log_loss"]
    fresh = model.predict(snowflake(NOW - timedelta(hours=1)), now=NOW, current_streak=0)
    old = model.predict(min(model.counts), now=NOW, current_streak=0, minimum_samples=1)
    assert fresh["ready"] and fresh["pooled"]
    assert fresh["model"] == "pattern_ml"
    assert 0 < old["hit_percent"] < fresh["hit_percent"] < 100
    assert fresh["hit_at"] >= 1


def test_trained_model_json_round_trip_preserves_predictions(new_channel_model, monkeypatch):
    state = json.loads(json.dumps(new_channel_model.to_state(), allow_nan=False))

    def forbid_training(*args, **kwargs):
        raise AssertionError("Restoring saved weights must not retrain")

    monkeypatch.setattr(GhostyPredictor, "__init__", forbid_training)
    restored = GhostyPredictor.from_state(state)
    channel = snowflake(NOW - timedelta(hours=1))
    assert restored.active
    assert restored.predict(channel, now=NOW, current_streak=0) == new_channel_model.predict(
        channel,
        now=NOW,
        current_streak=0,
    )


def test_random_results_do_not_enable_pattern_adjustments():
    random = np.random.default_rng(2026)
    rows = history(["hit" if value < 0.1 else "miss" for value in random.random(3000)])
    model = GhostyPredictor(rows, now=NOW)
    assert not model.active
    assert model.validation["status"] == "baseline_better"
    first = model.predict(CHANNEL, now=NOW, current_streak=0)
    later = model.predict(CHANNEL, now=NOW, current_streak=100)
    assert first["hit_percent"] == later["hit_percent"]


def test_features_first_seen_in_validation_do_not_enter_final_model():
    random = np.random.default_rng(3)
    rows = []
    for i in range(1000):
        stamp = BASE + timedelta(minutes=30 * i)
        channel = snowflake(stamp - timedelta(hours=1)) if i >= 600 else CHANNEL
        rows.append(
            {
                "channel_id": str(channel),
                "created_at": stamp,
                "result_message_id": str(i + 1),
                "outcome": "hit" if random.random() < 0.3 else "miss",
            }
        )
    model = GhostyPredictor(rows, now=NOW)
    assert model.mask[5] == 0


def test_pattern_predictions_can_be_disabled():
    model = GhostyPredictor(history(["hit", "miss"] * 300), now=NOW, enabled=False)
    assert not model.active
    assert model.validation["status"] == "disabled"
    assert model.predict(CHANNEL, now=NOW, current_streak=10)["hit_percent"] == 50


def test_bounded_training_history_does_not_limit_all_time_sample_threshold():
    model = GhostyPredictor(history(["hit", "miss"] * 50), now=NOW, enabled=False)
    prediction = model.predict(
        CHANNEL,
        now=NOW,
        current_streak=0,
        minimum_samples=100_000,
        historical_counts={"total": 100_000, "hits": 10_000},
    )
    assert prediction["ready"]
    assert prediction["samples"] == 100_000
    assert prediction["training_samples"] == 100
    assert prediction["hit_percent"] == 10
    assert prediction["hit_at"] == 10


def test_model_can_learn_reset_time_pattern_when_it_is_observable():
    random = np.random.default_rng(72)
    rows = []
    for i in range(20 * 48):
        stamp = BASE + timedelta(minutes=30 * i)
        rows.append(
            {
                "channel_id": str(CHANNEL),
                "created_at": stamp,
                "result_message_id": str(i + 1),
                "outcome": "hit" if random.random() < (0.6 if stamp.hour < 6 else 0.05) else "miss",
            }
        )
    model = GhostyPredictor(rows, now=NOW)
    assert model.active
    early = model.predict(CHANNEL, now=NOW + timedelta(hours=2), current_streak=0)
    late = model.predict(CHANNEL, now=NOW + timedelta(hours=14), current_streak=0)
    assert early["hit_percent"] > late["hit_percent"]
