from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

from app.database.repositories import ghosty_repository as repository
from app.services.ghosty_prediction import Context, GhostyPredictor, utc


class GhostyForecasts:
    def _observe_live_result(self, guild_id: int, result: dict[str, Any]) -> None:
        channel_id = int(result["channel_id"])
        contexts = self._live_contexts.setdefault(guild_id, {})
        if channel_id not in contexts:
            model = self._live_models.get(guild_id)
            contexts[channel_id] = (
                replace(model.contexts.get(channel_id, Context())) if model else Context()
            )
        context = contexts[channel_id]
        stamp = utc(result["created_at"])
        if context.last_gamble_at is not None and stamp < utc(context.last_gamble_at):
            return  # Backfilled older results are incorporated by retraining.
        context.roll_day(stamp)
        hit = result["outcome"] == "hit"
        stats = result["stats"]
        context.streak = (
            int(stats.get("last_streak", 0)) if hit else max(0, int(stats.get("streak", 1)) - 1)
        )
        context.session_streak = min(context.session_streak, context.streak)
        context.observe(hit, at=stamp)

    def get_live_prediction_data(
        self,
        guild_id: int,
        channel_id: int,
        *,
        stats: dict[str, Any],
        minimum_samples: int = 50,
        last_gamble_at: datetime | None = None,
    ) -> dict[str, Any]:
        """Predict from published weights and current counters without any I/O."""
        now = datetime.now(timezone.utc)
        predictor = self._live_models.get(guild_id)
        if predictor is None:
            predictor = GhostyPredictor([], now=now, enabled=False)
        prediction = predictor.predict(
            channel_id,
            now=now,
            current_streak=int(stats.get("streak", 0)),
            minimum_samples=minimum_samples,
            historical_counts={
                "total": int(stats.get("total_gambles", 0)),
                "hits": int(stats.get("hits", 0)),
            },
            last_gamble_at=last_gamble_at,
            context_override=self._live_contexts.get(guild_id, {}).get(channel_id),
        )
        return self._fixed_streak_prediction(
            guild_id,
            channel_id,
            stats=stats,
            prediction=prediction,
            now=now,
        )

    @staticmethod
    def _valid_streak_forecast(value: Any, base: int) -> bool:
        return (
            isinstance(value, dict)
            and value.get("version") == 3
            and value.get("streak_base") == base
            and isinstance(value.get("candidates"), list)
            and bool(value["candidates"])
            and all(
                isinstance(candidate, dict)
                and type(candidate.get("attempt")) is int
                and candidate["attempt"] > 0
                and type(candidate.get("probability")) in (int, float)
                and 0 < candidate["probability"] <= 1
                for candidate in value["candidates"]
            )
        )

    @staticmethod
    def _display_streak_forecast(
        prediction: dict[str, Any], forecast: dict[str, Any], streak: int
    ) -> dict[str, Any]:
        candidates = forecast["candidates"]
        remaining = [candidate for candidate in candidates if candidate["attempt"] > streak]
        if remaining:
            selected = min(
                remaining, key=lambda candidate: (-candidate["probability"], candidate["attempt"])
            )
        else:
            selected = max(candidates, key=lambda candidate: candidate["attempt"])
        return {
            **prediction,
            "hit_at": selected["attempt"],
            "candidate_percent": selected["probability"] * 100,
            "hit_interval": forecast.get("hit_interval"),
            "streak_forecast": forecast,
            "hit_candidates": [
                {**candidate, "status": "missed" if candidate["attempt"] <= streak else "pending"}
                for candidate in candidates
            ],
            "estimate_status": "pending" if remaining else "missed",
        }

    def _fixed_streak_prediction(
        self,
        guild_id: int,
        channel_id: int,
        *,
        stats: dict[str, Any],
        prediction: dict[str, Any],
        now: datetime,
    ) -> dict[str, Any]:
        """Freeze candidate outcomes; advance only when candidates have passed."""
        key = (guild_id, channel_id)
        streak = max(0, int(stats.get("streak", 0)))
        base = int(stats.get("total_gambles", 0)) - streak
        prediction = {**prediction, "streak_base": base}

        stored = stats.get("streak_forecast")
        if self._valid_streak_forecast(stored, base):
            forecast = dict(stored)
            self._saved_forecast_bases[key] = base
        elif self._pending_forecasts.get(key, (None, None))[0] == base:
            return {**prediction, "ready": False, "hit_at": None, "hit_candidates": []}
        elif self._valid_streak_forecast(self._streak_forecasts.get(key), base):
            forecast = self._streak_forecasts[key]
        else:
            self._streak_forecasts.pop(key, None)
            self._saved_forecast_bases.pop(key, None)
            if not prediction.get("ready") or not prediction.get("hit_candidates"):
                return {**prediction, "hit_at": None}
            candidates = [
                dict(candidate)
                for candidate in prediction["hit_candidates"]
                if candidate["attempt"] > streak
            ]
            if not candidates:
                return prediction
            initial_target = min(
                candidates, key=lambda candidate: (-candidate["probability"], candidate["attempt"])
            )
            forecast = {
                "version": 3,
                "streak_base": base,
                "candidates": candidates,
                "hit_at": initial_target["attempt"],
                "issued_after": streak,
                "hit_interval": list(prediction["hit_interval"])
                if prediction.get("hit_interval")
                else None,
                "issued_at": now,
                "samples": int(prediction.get("samples", 0)),
                "model": prediction.get("candidate_model", prediction.get("model", "historical")),
            }
        self._streak_forecasts[key] = forecast
        return self._display_streak_forecast(prediction, forecast, streak)

    async def save_streak_prediction(
        self,
        guild_id: int,
        channel_id: int,
        *,
        stats: dict[str, Any],
        prediction: dict[str, Any],
    ) -> dict[str, Any]:
        """Persist a newly issued candidate set once, before displaying it."""
        forecast = prediction.get("streak_forecast")
        if forecast is None:
            return prediction
        key = (guild_id, channel_id)
        base = forecast["streak_base"]
        async with self._forecast_locks.setdefault(key, asyncio.Lock()):
            if self._saved_forecast_bases.get(key) != base:
                stored = await repository.claim_streak_forecast(guild_id, channel_id, forecast)
                if self._valid_streak_forecast(stored, base):
                    self._streak_forecasts[key] = stored
                    self._saved_forecast_bases[key] = base
            winner = self._streak_forecasts.get(key, forecast)
            if winner["streak_base"] != base:
                return prediction  # This snapshot belongs to an older run.
            return self._display_streak_forecast(prediction, winner, int(stats.get("streak", 0)))

    def invalidate_prediction_cache(self, guild_id: int) -> None:
        self._prediction_cache.pop(guild_id, None)
        self._prediction_versions[guild_id] = self._prediction_versions.get(guild_id, 0) + 1

    async def _get_prediction_model(
        self, guild_id: int, *, refresh: bool = False
    ) -> GhostyPredictor:
        lock = self._prediction_locks.setdefault(guild_id, asyncio.Lock())
        async with lock:
            cached = self._prediction_cache.get(guild_id)
            if refresh or cached is None or cached[0] <= time.monotonic():
                version = self._prediction_versions.get(guild_id, 0)
                enabled = bool(await repository.get_config(guild_id, "prediction_patterns", True))
                history = await repository.get_prediction_history(guild_id)
                predictor = await asyncio.to_thread(
                    self._training_store.load_or_train,
                    guild_id,
                    history,
                    now=datetime.now(timezone.utc),
                    enabled=enabled,
                    current=cached[1] if cached is not None else None,
                )
                # A result arriving during training must not leave a stale
                # snapshot cached for every subsequent dashboard row.
                if version == self._prediction_versions.get(guild_id, 0):
                    self._prediction_cache[guild_id] = (time.monotonic() + 60, predictor)
                # Publish complete immutable weights even if newer results
                # arrived during fitting, then replay only untrained results.
                self._live_models[guild_id] = predictor
                included = {int(row.get("result_message_id") or 0) for row in history}
                for key, (_, result_id) in list(self._pending_forecasts.items()):
                    if key[0] == guild_id and result_id in included:
                        del self._pending_forecasts[key]
                recent = self._recent_results.setdefault(guild_id, OrderedDict())
                for result_id in list(recent):
                    if result_id in included:
                        del recent[result_id]
                self._live_contexts[guild_id] = {}
                for event in sorted(recent.values(), key=lambda item: utc(item["created_at"])):
                    self._observe_live_result(guild_id, event)
            else:
                predictor = cached[1]
            return predictor

    async def get_prediction_data(
        self,
        guild_id: int,
        channel_id: int,
        *,
        minimum_samples: int = 50,
        stats: dict[str, Any] | None = None,
        refresh_model: bool = False,
    ) -> dict[str, Any]:
        """Use validated pattern estimates, otherwise retain historical rates."""
        if refresh_model:
            predictor = await self._get_prediction_model(guild_id, refresh=True)
        else:
            predictor = await self._get_prediction_model(guild_id)
        if stats is None:
            stats = await self.get_stats(guild_id, channel_id)
        # A bounded training window must not make a larger, existing minimum
        # sample threshold impossible or discard an inactive channel's rates.
        historical_counts = None
        if int(stats.get("total_gambles", 0)) > 0 and "hits" in stats:
            historical_counts = {"total": int(stats["total_gambles"]), "hits": int(stats["hits"])}
        elif predictor.samples >= 20_000:
            historical_counts = await repository.get_gamble_counts(guild_id, channel_id)
        now = datetime.now(timezone.utc)
        last_gamble_at = None
        if (
            channel_id not in predictor.contexts
            and channel_id not in self._live_contexts.get(guild_id, {})
            and historical_counts is not None
            and historical_counts["total"] > 0
        ):
            last_gamble_at = await repository.get_last_gamble_at(guild_id, channel_id, before=now)
        prediction = predictor.predict(
            channel_id,
            now=now,
            current_streak=stats["streak"],
            minimum_samples=minimum_samples,
            historical_counts=historical_counts,
            last_gamble_at=last_gamble_at,
            context_override=self._live_contexts.get(guild_id, {}).get(channel_id),
        )
        prediction = self._fixed_streak_prediction(
            guild_id,
            channel_id,
            stats=stats,
            prediction=prediction,
            now=now,
        )
        return await self.save_streak_prediction(
            guild_id, channel_id, stats=stats, prediction=prediction
        )
