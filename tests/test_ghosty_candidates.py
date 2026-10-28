import numpy as np
import pytest

from app.services.ghosty_prediction import first_hit_candidates


def test_candidates_use_exact_first_hit_probabilities_and_leave_other_outcomes_unassigned():
    hazards = np.zeros(200)
    hazards[7] = 0.4
    hazards[12] = 0.3 / 0.6
    hazards[21] = 0.2 / 0.3
    candidates = first_hit_candidates(hazards, current_streak=0)
    assert [candidate["attempt"] for candidate in candidates] == [8, 13, 22]
    assert [candidate["probability"] for candidate in candidates] == pytest.approx([0.4, 0.3, 0.2])
    assert sum(candidate["probability"] for candidate in candidates) == pytest.approx(0.9)


def test_constant_hazard_candidates_have_geometric_probabilities_with_absolute_attempts():
    candidates = first_hit_candidates(np.full(200, 0.1), current_streak=7)
    assert [candidate["attempt"] for candidate in candidates] == [8, 9, 10, 11, 12]
    assert [candidate["probability"] for candidate in candidates] == pytest.approx([0.1, 0.09, 0.081, 0.0729, 0.06561])


@pytest.mark.parametrize("hazards, expected", [
    (np.zeros(200), []),
    (np.ones(200), [{"attempt": 6, "probability": 1.0}]),
    (np.array([]), []),
])
def test_impossible_candidates_are_not_created(hazards, expected):
    assert first_hit_candidates(hazards, current_streak=5) == expected


def test_equal_probability_candidates_prefer_the_earlier_attempt():
    # Exact masses at attempts one and two both equal one quarter.
    candidates = first_hit_candidates(np.array([0.25, 1 / 3, 0]), current_streak=0)
    assert [candidate["attempt"] for candidate in candidates] == [1, 2]
    assert [candidate["probability"] for candidate in candidates] == pytest.approx([0.25, 0.25])
