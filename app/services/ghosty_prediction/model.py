from __future__ import annotations

from collections import Counter
from dataclasses import replace
from datetime import datetime
from typing import Any

import numpy as np

from .constants import (
    FEATURES,
    FORECAST_HORIZON,
    NEW_CHANNEL_HOURS,
    STREAK_POOL_STRENGTH,
)
from .features import (
    Context,
    channel_age_hours,
    feature_vector,
    first_hit_candidates,
    fit_logistic,
    logit,
    losses,
    rates,
    session_day,
    sigmoid,
    supported_features,
    utc,
)
from .serialization import PredictorState


class GhostyPredictor(PredictorState):
    def __init__(self, history: list[dict[str, Any]], *, now: datetime, enabled: bool = True):
        now = utc(now)
        self.trained_at = now
        self.patterns_enabled = enabled
        self.training_fingerprint: str | None = None
        rows = [
            row
            for row in history
            if isinstance(row.get("created_at"), datetime)
            and row.get("outcome") in {"hit", "miss"}
            and str(row.get("channel_id", "")).isdigit()
            and utc(row["created_at"]) <= now
        ]
        rows.sort(key=lambda row: (utc(row["created_at"]), int(row.get("result_message_id") or 0)))
        self.contexts: dict[int, Context] = {}
        channels, vectors, outcomes, days = [], [], [], []
        self.counts: dict[int, Counter] = {}
        self.streak_risk: dict[int, Counter] = {}
        self.streak_hits: dict[int, Counter] = {}
        for row in rows:
            channel_id = int(row["channel_id"])
            stamp = utc(row["created_at"])
            context = self.contexts.setdefault(channel_id, Context())
            context.roll_day(stamp)
            # Construct features before revealing this result to its context.
            vectors.append(feature_vector(context, channel_id, stamp))
            channels.append(channel_id)
            days.append(session_day(stamp))
            hit = row["outcome"] == "hit"
            risk = self.streak_risk.setdefault(channel_id, Counter())
            hits = self.streak_hits.setdefault(channel_id, Counter())
            # The start of the first observed run is unknown. Only count
            # attempts after a recorded hit establishes a real run boundary.
            if context.saw_hit:
                attempt = context.streak + 1
                risk[attempt] += 1
                if hit:
                    hits[attempt] += 1
            outcomes.append(float(hit))
            self.counts.setdefault(channel_id, Counter())["total"] += 1
            self.counts[channel_id]["hits"] += hit
            context.observe(hit, at=stamp)

        self.samples = len(rows)
        self._pool_streak_history()
        self.weights = np.zeros(len(FEATURES))
        self.mask = np.zeros(len(FEATURES))
        self.active = False
        self.validation: dict[str, Any] = {
            "status": "disabled" if not enabled else "insufficient_data"
        }
        y = np.asarray(outcomes, dtype=float)
        self.pooled_rate, self.channel_rates = rates(channels, y)
        if not enabled or len(rows) < 300 or y.sum() < 20 or (1 - y).sum() < 20:
            return

        x = np.asarray(vectors, dtype=float)
        first_split = int(len(rows) * 0.6)
        # Select supported features using only the earliest training window.
        # A newly eligible feature must not enter the final model untested.
        mask = supported_features(x[:first_split], y[:first_split], set(days[:first_split]))
        model_scores, baseline_scores, historical_scores, fold_results = [], [], [], []
        for start, end in (
            (int(len(rows) * 0.6), int(len(rows) * 0.8)),
            (int(len(rows) * 0.8), len(rows)),
        ):
            train_y, test_y = y[:start], y[start:end]
            if train_y.sum() < 10 or test_y.sum() < 3 or (1 - test_y).sum() < 3:
                return
            pooled, channel_rates = rates(channels[:start], train_y)
            offsets = logit(np.asarray([channel_rates[channel] for channel in channels[:start]]))
            weights = fit_logistic(x[:start] * mask, train_y, offsets)
            baseline = np.asarray(
                [channel_rates.get(channel, pooled) for channel in channels[start:end]]
            )
            totals = Counter(channels[:start])
            hits = Counter(channel for channel, hit in zip(channels[:start], train_y) if hit)
            historical = np.asarray(
                [
                    hits[channel] / totals[channel] if totals[channel] else pooled
                    for channel in channels[start:end]
                ]
            )
            predicted = sigmoid(logit(baseline) + (x[start:end] * mask) @ weights)
            model_loss, _ = losses(test_y, predicted)
            baseline_loss, _ = losses(test_y, baseline)
            historical_loss, _ = losses(test_y, historical)
            fold_results.append(model_loss < min(baseline_loss, historical_loss))
            model_scores.extend(predicted)
            baseline_scores.extend(baseline)
            historical_scores.extend(historical)

        test_y = y[int(len(rows) * 0.6) :]
        model_loss, model_brier = losses(test_y, np.asarray(model_scores))
        base_loss, base_brier = losses(test_y, np.asarray(baseline_scores))
        historical_loss, historical_brier = losses(test_y, np.asarray(historical_scores))
        self.active = (
            all(fold_results)
            and model_loss <= min(base_loss, historical_loss) * 0.99
            and model_brier < min(base_brier, historical_brier)
        )
        self.validation = {
            "status": "validated" if self.active else "baseline_better",
            "samples": len(test_y),
            "model_log_loss": model_loss,
            "baseline_log_loss": base_loss,
            "model_brier": model_brier,
            "baseline_brier": base_brier,
            "historical_log_loss": historical_loss,
            "historical_brier": historical_brier,
        }
        self.mask = mask
        if self.active:
            offsets = logit(np.asarray([self.channel_rates[channel] for channel in channels]))
            self.weights = fit_logistic(x * self.mask, y, offsets)

    def _pool_streak_history(self) -> None:
        self._pooled_streak_risk = Counter()
        self._pooled_streak_hits = Counter()
        for values in self.streak_risk.values():
            self._pooled_streak_risk.update(values)
        for values in self.streak_hits.values():
            self._pooled_streak_hits.update(values)

    def _streak_outcome_candidates(
        self,
        channel_id: int,
        streak: int,
        context_hazards: np.ndarray,
        prior: float,
    ) -> list[dict[str, Any]]:
        """Learn possible hit positions from observed runs in the shared model.

        Attempt counts include unfinished runs, so a miss at a position lowers
        its estimate on retraining. Other channels provide a modest prior;
        channels with their own completed runs propose their own hit positions.
        """
        channel_hits = self.streak_hits.get(channel_id, Counter())
        allowed = set(channel_hits or self._pooled_streak_hits)
        future = [attempt for attempt in allowed if attempt > streak]
        if not future:
            return []
        positions = np.arange(streak + 1, max(future) + 1)
        channel_risk = self.streak_risk.get(channel_id, Counter())
        local_risk = np.asarray([channel_risk[int(attempt)] for attempt in positions], dtype=float)
        local_hits = np.asarray([channel_hits[int(attempt)] for attempt in positions], dtype=float)
        other_risk = (
            np.asarray([self._pooled_streak_risk[int(attempt)] for attempt in positions])
            - local_risk
        )
        other_hits = (
            np.asarray([self._pooled_streak_hits[int(attempt)] for attempt in positions])
            - local_hits
        )
        # Jeffreys smoothing avoids treating a small sample as certainty. Use
        # only other channels for the pooled prior to avoid counting data twice.
        shared = (other_hits + 0.5) / (other_risk + 1)
        hazards = np.where(
            other_risk > 0,
            (local_hits + STREAK_POOL_STRENGTH * shared) / (local_risk + STREAK_POOL_STRENGTH),
            (local_hits + 0.5) / (local_risk + 1),
        )
        if self.active:
            # Only validated context effects may adjust the empirical curve.
            indices = np.minimum(np.arange(len(positions)), len(context_hazards) - 1)
            adjustment = logit(context_hazards[indices]) - logit(np.asarray(prior))
            hazards = sigmoid(logit(hazards) + adjustment)
        return first_hit_candidates(hazards, current_streak=streak, allowed_attempts=allowed)

    def predict(
        self,
        channel_id: int,
        *,
        now: datetime,
        current_streak: int,
        minimum_samples: int = 50,
        historical_counts: dict[str, int] | None = None,
        last_gamble_at: datetime | None = None,
        context_override: Context | None = None,
    ) -> dict[str, Any]:
        count = (
            Counter(historical_counts)
            if historical_counts is not None
            else self.counts.get(channel_id, Counter())
        )
        pooled = (
            self.active
            and bool(self.mask[5])
            and channel_age_hours(channel_id, now) < NEW_CHANNEL_HOURS
            and count["total"] < minimum_samples
        )
        ready = count["total"] >= max(1, minimum_samples) or pooled
        result = {
            "ready": ready,
            "samples": count["total"],
            "training_samples": self.samples,
            "minimum_samples": minimum_samples,
            "hit_percent": None,
            "hit_at": None,
            "hit_interval": None,
            "hit_candidates": [],
            "model": "pattern_ml" if self.active else "historical",
            "candidate_model": "channel_streak_history"
            if self.streak_hits.get(channel_id)
            else "pooled_streak_history",
            "pooled": pooled,
            "validation": self.validation,
        }
        if not ready:
            return result

        streak = max(0, current_streak)
        context = replace(
            context_override
            if context_override is not None
            else self.contexts.get(channel_id, Context())
        )
        if last_gamble_at is not None:
            context.last_gamble_at = utc(last_gamble_at)
        context.roll_day(now)
        context.streak = streak
        context.session_streak = min(context.session_streak, streak)
        prior = self.channel_rates.get(
            channel_id,
            (count["hits"] + 20 * self.pooled_rate) / (count["total"] + 20),
        )
        if self.active:
            vectors = []
            for step in range(FORECAST_HORIZON + 1):
                future = replace(
                    context,
                    streak=streak + step,
                    session_streak=context.session_streak + step,
                    # Conditional attempts after the next one follow an assumed
                    # miss now; their last gamble is no longer the historical one.
                    last_gamble_at=context.last_gamble_at if step == 0 else utc(now),
                )
                vectors.append(feature_vector(future, channel_id, now))
            hazards = sigmoid(
                logit(np.asarray([prior]))[0] + (np.asarray(vectors) * self.mask) @ self.weights
            )
        else:
            hazards = np.full(FORECAST_HORIZON + 1, count["hits"] / count["total"])

        probability = float(hazards[0])
        result["hit_percent"] = round(probability * 100, 2)
        result["hit_candidates"] = self._streak_outcome_candidates(
            channel_id, streak, hazards, prior
        )
        if probability <= 0:
            return result
        survival = np.cumprod(1 - hazards[:FORECAST_HORIZON])
        # The streak features are capped at 200, making the remaining tail
        # constant. Future attempts are assumed to share the current session.
        expected = float(1 + survival[:-1].sum() + survival[-1] / hazards[-1])
        result["hit_at"] = streak + max(1, round(expected))
        bounds = [np.flatnonzero(1 - survival >= quantile) for quantile in (0.25, 0.75)]
        if all(len(bound) for bound in bounds):
            result["hit_interval"] = tuple(streak + int(bound[0]) + 1 for bound in bounds)
        return result
