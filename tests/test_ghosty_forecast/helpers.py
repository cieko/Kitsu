from __future__ import annotations

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock


def model(target=8):
    def predict(channel_id, *, current_streak, historical_counts=None, **kwargs):
        count = historical_counts or {"total": 80, "hits": 10}
        return {
            "ready": count["total"] >= kwargs.get("minimum_samples", 50),
            "hit_at": current_streak + target,
            "hit_candidates": [
                {"attempt": current_streak + target + step, "probability": probability}
                for step, probability in ((0, 0.4), (5, 0.3), (14, 0.2))
            ],
            "hit_interval": (current_streak + 3, current_streak + 12),
            "hit_percent": round(count["hits"] / count["total"] * 100, 2),
            "samples": count["total"],
            "model": "historical",
        }

    return SimpleNamespace(predict=MagicMock(side_effect=predict), contexts={}, samples=80)


def forecast(target=8, base=80):
    return {
        "version": 3,
        "streak_base": base,
        "hit_at": target,
        "candidates": [
            {"attempt": target + step, "probability": probability}
            for step, probability in ((0, 0.4), (5, 0.3), (14, 0.2))
        ],
    }


def prepare(module, monkeypatch):
    documents = {(1, 2): {"streak": 0, "total_gambles": 80, "hits": 10}}

    async def claim(guild_id, channel_id, forecast):
        await asyncio.sleep(0)
        stats = documents.get((guild_id, channel_id))
        if stats is None or stats["total_gambles"] - stats["streak"] != forecast["streak_base"]:
            return None
        existing = stats.get("streak_forecast")
        if (
            existing is None
            or existing["streak_base"] != forecast["streak_base"]
            or existing.get("version") != 3
        ):
            stats["streak_forecast"] = deepcopy(forecast)
        return deepcopy(stats["streak_forecast"])

    monkeypatch.setattr(module.repository, "claim_streak_forecast", AsyncMock(side_effect=claim))
    monkeypatch.setattr(module.repository, "get_last_gamble_at", AsyncMock(return_value=None))
    monkeypatch.setattr(
        module.repository,
        "get_channel_stats",
        AsyncMock(
            side_effect=lambda guild_id, channel_id: deepcopy(
                documents.get((guild_id, channel_id))
            ),
        ),
    )
    service = module.GhostyService(None)
    predictor = model()
    service._live_models[1] = predictor
    service._get_prediction_model = AsyncMock(return_value=predictor)
    return service, documents
