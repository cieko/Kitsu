from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json

import pytest

from app.services.ghosty_prediction import GhostyPredictor


NOW = datetime.now(timezone.utc)
BASE = NOW - timedelta(days=30)


def rows_for_runs(lengths, *, channel_id=2, first_id=1, trailing_misses=0, prefix_misses=0):
    outcomes = ["miss"] * prefix_misses + ["hit"]
    for length in lengths:
        outcomes += ["miss"] * (length - 1) + ["hit"]
    outcomes += ["miss"] * trailing_misses
    return [{"channel_id": str(channel_id), "result_message_id": str(first_id + index),
             "created_at": BASE + timedelta(seconds=first_id + index), "outcome": outcome}
            for index, outcome in enumerate(outcomes)]


def test_candidates_come_from_observed_outcomes_and_favor_the_most_likely_peak():
    model = GhostyPredictor(rows_for_runs([8] * 6 + [13] * 20 + [22] * 10), now=NOW, enabled=False)
    result = model.predict(2, now=NOW, current_streak=0)
    candidates = result["hit_candidates"]
    assert {candidate["attempt"] for candidate in candidates} == {8, 13, 22}
    assert candidates[0]["attempt"] == 13
    assert candidates[0]["probability"] == max(candidate["probability"] for candidate in candidates)
    assert sum(candidate["probability"] for candidate in candidates) < 1
    assert result["candidate_model"] == "channel_streak_history"


def test_shared_model_retains_channel_specific_possible_outcomes():
    first = rows_for_runs([8] * 10 + [13] * 5 + [22] * 5)
    second = rows_for_runs([4] * 10 + [6] * 5 + [9] * 5, channel_id=3, first_id=len(first) + 1)
    model = GhostyPredictor(first + second, now=NOW, enabled=False)
    assert {candidate["attempt"] for candidate in model.predict(2, now=NOW, current_streak=0)["hit_candidates"]} == {8, 13, 22}
    assert {candidate["attempt"] for candidate in model.predict(3, now=NOW, current_streak=0)["hit_candidates"]} == {4, 6, 9}
    borrowed = model.predict(4, now=NOW, current_streak=0, historical_counts={"total": 100, "hits": 10})
    assert borrowed["candidate_model"] == "pooled_streak_history"
    assert borrowed["hit_candidates"]
    assert all(candidate["attempt"] in {4, 6, 8, 9, 13, 22} for candidate in borrowed["hit_candidates"])


def test_unfinished_run_misses_lower_the_future_estimate_without_faking_a_hit():
    original = GhostyPredictor(rows_for_runs([8] * 10), now=NOW, enabled=False)
    updated = GhostyPredictor(rows_for_runs([8] * 10, trailing_misses=8), now=NOW, enabled=False)
    assert updated.streak_risk[2][8] == original.streak_risk[2][8] + 1
    assert updated.streak_hits[2][8] == original.streak_hits[2][8] == 10
    before = original.predict(2, now=NOW, current_streak=0)["hit_candidates"][0]
    after = updated.predict(2, now=NOW, current_streak=0)["hit_candidates"][0]
    assert before["attempt"] == after["attempt"] == 8
    assert after["probability"] < before["probability"]
    assert not updated.predict(2, now=NOW, current_streak=8)["hit_candidates"]


def test_first_partial_streak_does_not_create_a_false_possible_outcome():
    model = GhostyPredictor(rows_for_runs([8] * 6, prefix_misses=40), now=NOW, enabled=False)
    assert model.streak_hits[2] == {8: 6}
    assert [candidate["attempt"] for candidate in model.predict(2, now=NOW, current_streak=0)["hit_candidates"]] == [8]


def test_streak_outcome_statistics_survive_json_restore_without_retraining(monkeypatch):
    model = GhostyPredictor(rows_for_runs([8, 13, 22] * 10), now=NOW, enabled=False)
    state = json.loads(json.dumps(model.to_state()))
    monkeypatch.setattr(GhostyPredictor, "__init__", lambda *args, **kwargs: pytest.fail("Unexpected retraining"))
    restored = GhostyPredictor.from_state(state)
    assert restored.predict(2, now=NOW, current_streak=3) == model.predict(2, now=NOW, current_streak=3)


def test_invalid_saved_streak_statistics_are_rejected():
    model = GhostyPredictor(rows_for_runs([8] * 10), now=NOW, enabled=False)
    state = deepcopy(model.to_state())
    state["streak_hits"]["2"]["8"] = 1000
    with pytest.raises(ValueError, match="streak outcome"):
        GhostyPredictor.from_state(state)


def test_long_observed_outcomes_remain_possible_beyond_the_context_feature_cap():
    model = GhostyPredictor(rows_for_runs([250] * 3), now=NOW, enabled=False)
    candidates = model.predict(2, now=NOW, current_streak=0)["hit_candidates"]
    assert [candidate["attempt"] for candidate in candidates] == [250]


@pytest.mark.parametrize("dominant", [1, 4, 13])
def test_initial_target_follows_recorded_outcomes_instead_of_defaulting_to_two(dominant):
    model = GhostyPredictor(rows_for_runs([dominant] * 30 + [22] * 3), now=NOW, enabled=False)
    assert model.predict(2, now=NOW, current_streak=0)["hit_candidates"][0]["attempt"] == dominant


def test_the_shared_model_can_issue_up_to_five_real_outcomes():
    possibilities = [2, 4, 8, 13, 22, 30]
    model = GhostyPredictor(rows_for_runs(possibilities * 10), now=NOW, enabled=False)
    candidates = model.predict(2, now=NOW, current_streak=0)["hit_candidates"]
    assert len(candidates) == 5
    assert {candidate["attempt"] for candidate in candidates} <= set(possibilities)
    assert candidates[0]["probability"] == max(candidate["probability"] for candidate in candidates)
