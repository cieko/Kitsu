from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

import numpy as np

from .constants import (
    CHANNEL_AGE_CAP_HOURS,
    FEATURES,
    GAMBLE_GAP_CAP_HOURS,
    IST,
    LONG_STREAK_MISSES,
    MAX_HIT_CANDIDATES,
    NEW_CHANNEL_HOURS,
    RESET_OFFSET,
    SHORT_WINDOW,
)


def utc(value: datetime) -> datetime:
    # PyMongo returns naive UTC datetimes unless tz_aware is enabled.
    return (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )


def session_day(value: datetime) -> date:
    """05:30 IST belongs to the new prediction day; 05:29 does not."""
    return (utc(value).astimezone(IST) - RESET_OFFSET).date()


def channel_created_at(channel_id: int) -> datetime:
    return datetime.fromtimestamp(((channel_id >> 22) + 1420070400000) / 1000, timezone.utc)


def channel_age_hours(channel_id: int, now: datetime) -> float:
    return max(0.0, (utc(now) - channel_created_at(channel_id)).total_seconds() / 3600)


def reset_phase(value: datetime) -> float:
    local = utc(value).astimezone(IST) - RESET_OFFSET
    seconds = local.hour * 3600 + local.minute * 60 + local.second + local.microsecond / 1_000_000
    return 2 * math.pi * seconds / 86400


@dataclass
class Context:
    streak: int = 0
    previous_streak: int | None = None
    saw_hit: bool = False
    day: date | None = None
    session_streak: int = 0
    last_gamble_at: datetime | None = None

    def roll_day(self, now: datetime) -> None:
        day = session_day(now)
        if day != self.day:
            self.day = day
            self.session_streak = 0

    def observe(self, hit: bool, *, at: datetime | None = None) -> None:
        if at is not None:
            self.last_gamble_at = utc(at)
        if hit:
            # The first observed run may start partway through a streak.
            if self.saw_hit:
                self.previous_streak = self.streak
            self.saw_hit = True
            self.streak = self.session_streak = 0
        else:
            self.streak += 1
            self.session_streak += 1


def feature_vector(context: Context, channel_id: int, now: datetime) -> list[float]:
    now = utc(now)
    phase = reset_phase(now)
    created = channel_created_at(channel_id)
    age = channel_age_hours(channel_id, now)
    last = utc(context.last_gamble_at) if context.last_gamble_at is not None else None
    if last is not None and last > now:
        last = None
    gap = (now - last).total_seconds() / 3600 if last is not None else 0.0
    last_phase = reset_phase(last) if last is not None else 0.0
    after_long = (
        context.previous_streak is not None and context.previous_streak >= LONG_STREAK_MISSES
    )
    next_attempt = context.streak + 1
    scale = math.log1p(200)
    return [
        1.0,
        math.log1p(min(context.streak, 200)) / scale,
        math.sin(phase),
        math.cos(phase),
        math.log1p(min(context.session_streak, 200)) / scale,
        float(age < NEW_CHANNEL_HOURS),
        float(after_long),
        float(after_long and SHORT_WINDOW[0] <= next_attempt <= SHORT_WINDOW[1]),
        math.log1p(min(age, CHANNEL_AGE_CAP_HOURS)) / math.log1p(CHANNEL_AGE_CAP_HOURS),
        float(created <= now and session_day(created) == session_day(now)),
        float(last is not None),
        math.log1p(min(gap, GAMBLE_GAP_CAP_HOURS)) / math.log1p(GAMBLE_GAP_CAP_HOURS),
        float(last is not None and session_day(last) < session_day(now)),
        math.sin(last_phase) if last is not None else 0.0,
        math.cos(last_phase) if last is not None else 0.0,
    ]


def sigmoid(values: np.ndarray) -> np.ndarray:
    return 1 / (1 + np.exp(-np.clip(values, -30, 30)))


def logit(probabilities: np.ndarray) -> np.ndarray:
    probabilities = np.clip(probabilities, 0.001, 0.999)
    return np.log(probabilities / (1 - probabilities))


def rates(channels: list[int], outcomes: np.ndarray) -> tuple[float, dict[int, float]]:
    pooled = (float(outcomes.sum()) + 1) / (len(outcomes) + 2)
    totals = Counter(channels)
    hits = Counter(channel for channel, hit in zip(channels, outcomes) if hit)
    return pooled, {
        channel: (hits[channel] + 20 * pooled) / (total + 20) for channel, total in totals.items()
    }


def supported_features(x: np.ndarray, y: np.ndarray, days: set[date]) -> np.ndarray:
    mask = np.ones(len(FEATURES), dtype=float)
    # Constant channel metadata must not act as another intercept.
    mask[1:] = (np.ptp(x[:, 1:], axis=0) >= 1e-9).astype(float)
    for column in (5, 6, 7, 9, 10, 12):
        group = x[:, column] == 1
        # A single new channel or lucky result must not create a "boost".
        if group.sum() < 30 or (~group).sum() < 30 or y[group].sum() < 3 or y[~group].sum() < 3:
            mask[column] = 0
    observed = x[:, 10] == 1
    if observed.sum() < 30 or y[observed].sum() < 3:
        mask[11:15] = 0
    if len(days) < 3:
        mask[[2, 3, 4, 9, 12, 13, 14]] = 0
    return mask


def fit_logistic(x: np.ndarray, y: np.ndarray, offsets: np.ndarray) -> np.ndarray:
    """L2-regularised logistic regression with fixed channel-rate offsets."""
    weights = np.zeros(x.shape[1])
    penalty = np.full(x.shape[1], 5.0)
    penalty[0] = 0.001

    def objective(candidate: np.ndarray) -> float:
        scores = offsets + x @ candidate
        return float(
            np.sum(np.logaddexp(0, scores) - y * scores) + 0.5 * np.sum(penalty * candidate**2)
        )

    for _ in range(30):
        probability = sigmoid(offsets + x @ weights)
        gradient = x.T @ (probability - y) + penalty * weights
        hessian = x.T @ ((probability * (1 - probability))[:, None] * x) + np.diag(penalty)
        step = np.linalg.solve(hessian, gradient)
        old_objective = objective(weights)
        fraction = 1.0
        while fraction > 1 / 1024:
            candidate = weights - fraction * step
            if objective(candidate) <= old_objective:
                break
            fraction /= 2
        else:
            break
        weights = candidate
        if np.max(np.abs(fraction * step)) < 1e-6:
            break
    return weights


def losses(y: np.ndarray, probabilities: np.ndarray) -> tuple[float, float]:
    probabilities = np.clip(probabilities, 0.001, 0.999)
    return (
        float(np.mean(-(y * np.log(probabilities) + (1 - y) * np.log1p(-probabilities)))),
        float(np.mean((probabilities - y) ** 2)),
    )


def first_hit_candidates(
    hazards: np.ndarray,
    *,
    current_streak: int,
    limit: int = MAX_HIT_CANDIDATES,
    allowed_attempts: set[int] | None = None,
) -> list[dict[str, Any]]:
    """Rank exact next-hit outcomes, accounting for all preceding misses.

    These are probability masses, not conditional per-gamble chances. Keeping
    only the top outcomes does not redistribute the unlisted probability.
    """
    hazards = np.asarray(hazards, dtype=float)
    if not len(hazards):
        return []
    survival_before = np.concatenate(([1.0], np.cumprod(1 - hazards[:-1])))
    masses = survival_before * hazards
    order = np.argsort(-masses, kind="stable")
    eligible = [
        index
        for index in order
        if masses[index] > 0
        and (allowed_attempts is None or current_streak + int(index) + 1 in allowed_attempts)
    ]
    return [
        {"attempt": current_streak + int(index) + 1, "probability": float(masses[index])}
        for index in eligible[:limit]
    ]
