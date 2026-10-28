from __future__ import annotations

from collections import Counter
from dataclasses import asdict
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

import numpy as np

from .constants import (
    CHANNEL_AGE_CAP_HOURS,
    FEATURES,
    GAMBLE_GAP_CAP_HOURS,
    LONG_STREAK_MISSES,
    NEW_CHANNEL_HOURS,
    PREDICTOR_VERSION,
    SHORT_WINDOW,
    STREAK_POOL_STRENGTH,
)
from .features import (
    Context,
    utc,
)

if TYPE_CHECKING:
    from .model import GhostyPredictor


class PredictorState:
    @staticmethod
    def feature_settings() -> dict[str, Any]:
        return {
            "reset_ist": "05:30",
            "new_channel_hours": NEW_CHANNEL_HOURS,
            "long_streak_misses": LONG_STREAK_MISSES,
            "short_window": list(SHORT_WINDOW),
            "channel_age_cap_hours": CHANNEL_AGE_CAP_HOURS,
            "gamble_gap_cap_hours": GAMBLE_GAP_CAP_HOURS,
            "streak_pool_strength": STREAK_POOL_STRENGTH,
        }

    def to_state(self) -> dict[str, Any]:
        contexts = {}
        for channel, context in self.contexts.items():
            value = asdict(context)
            value["day"] = context.day.isoformat() if context.day else None
            value["last_gamble_at"] = (
                context.last_gamble_at.isoformat() if context.last_gamble_at else None
            )
            contexts[str(channel)] = value
        return {
            "version": PREDICTOR_VERSION,
            "features": list(FEATURES),
            "feature_settings": self.feature_settings(),
            "trained_at": self.trained_at.isoformat(),
            "patterns_enabled": self.patterns_enabled,
            "training_fingerprint": self.training_fingerprint,
            "samples": self.samples,
            "active": self.active,
            "weights": self.weights.tolist(),
            "mask": self.mask.tolist(),
            "pooled_rate": self.pooled_rate,
            "channel_rates": {str(channel): value for channel, value in self.channel_rates.items()},
            "counts": {str(channel): dict(value) for channel, value in self.counts.items()},
            "streak_risk": {
                str(channel): {str(attempt): count for attempt, count in values.items()}
                for channel, values in self.streak_risk.items()
            },
            "streak_hits": {
                str(channel): {str(attempt): count for attempt, count in values.items()}
                for channel, values in self.streak_hits.items()
            },
            "contexts": contexts,
            "validation": self.validation,
        }

    @classmethod
    def from_state(cls, state: dict[str, Any]) -> GhostyPredictor:
        """Restore JSON state without fitting or executing serialized code."""
        if (
            state["version"] != PREDICTOR_VERSION
            or state["features"] != list(FEATURES)
            or state["feature_settings"] != cls.feature_settings()
        ):
            raise ValueError("Saved predictor version or features do not match")
        model = cls.__new__(cls)
        model.weights = np.asarray(state["weights"], dtype=float)
        model.mask = np.asarray(state["mask"], dtype=float)
        if (
            model.weights.shape != (len(FEATURES),)
            or model.mask.shape != (len(FEATURES),)
            or not np.isfinite(model.weights).all()
            or not np.isin(model.mask, [0, 1]).all()
        ):
            raise ValueError("Invalid saved predictor coefficients")
        model.trained_at = utc(datetime.fromisoformat(state["trained_at"]))
        model.patterns_enabled = state["patterns_enabled"]
        model.training_fingerprint = state["training_fingerprint"]
        model.samples = state["samples"]
        model.active = state["active"]
        model.validation = dict(state["validation"])
        model.pooled_rate = float(state["pooled_rate"])
        model.channel_rates = {
            int(channel): float(value) for channel, value in state["channel_rates"].items()
        }
        model.counts = {int(channel): Counter(value) for channel, value in state["counts"].items()}
        model.streak_risk = {
            int(channel): Counter({int(attempt): count for attempt, count in values.items()})
            for channel, values in state["streak_risk"].items()
        }
        model.streak_hits = {
            int(channel): Counter({int(attempt): count for attempt, count in values.items()})
            for channel, values in state["streak_hits"].items()
        }
        if (
            type(model.samples) is not int
            or model.samples < 0
            or set(model.streak_risk) != set(model.counts)
            or set(model.streak_hits) != set(model.counts)
            or any(
                attempt < 1 or type(count) is not int or count < 1
                for values in (model.streak_risk, model.streak_hits)
                for counts in values.values()
                for attempt, count in counts.items()
            )
            or any(
                count > model.streak_risk[channel][attempt]
                for channel, values in model.streak_hits.items()
                for attempt, count in values.items()
            )
            or sum(sum(values.values()) for values in model.streak_risk.values()) > model.samples
            or any(
                sum(model.streak_risk[channel].values()) > model.counts[channel]["total"]
                or sum(model.streak_hits[channel].values()) > model.counts[channel]["hits"]
                for channel in model.counts
            )
        ):
            raise ValueError("Invalid saved streak outcome statistics")
        model._pool_streak_history()
        model.contexts = {}
        for channel, value in state["contexts"].items():
            values = dict(value)
            values["day"] = date.fromisoformat(values["day"]) if values["day"] else None
            values["last_gamble_at"] = (
                utc(datetime.fromisoformat(values["last_gamble_at"]))
                if values["last_gamble_at"]
                else None
            )
            context = Context(**values)
            if (
                not isinstance(context.streak, int)
                or context.streak < 0
                or not isinstance(context.session_streak, int)
                or context.session_streak < 0
                or (
                    context.previous_streak is not None
                    and (
                        not isinstance(context.previous_streak, int) or context.previous_streak < 0
                    )
                )
                or (
                    context.last_gamble_at is not None and context.last_gamble_at > model.trained_at
                )
            ):
                raise ValueError("Invalid saved streak context")
            model.contexts[int(channel)] = context
        if (
            type(model.patterns_enabled) is not bool
            or type(model.active) is not bool
            or type(model.samples) is not int
            or model.samples < 0
            or not 0 < model.pooled_rate < 1
            or any(not 0 < rate < 1 for rate in model.channel_rates.values())
            or any(
                type(count["total"]) is not int
                or type(count["hits"]) is not int
                or not 0 <= count["hits"] <= count["total"]
                for count in model.counts.values()
            )
            or sum(count["total"] for count in model.counts.values()) != model.samples
            or set(model.contexts) != set(model.counts)
            or set(model.channel_rates) != set(model.counts)
            or (
                model.active
                and (not model.patterns_enabled or model.validation.get("status") != "validated")
            )
        ):
            raise ValueError("Invalid saved predictor statistics")
        return model
