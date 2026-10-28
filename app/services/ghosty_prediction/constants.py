from __future__ import annotations

from datetime import timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30), name="IST")


RESET_OFFSET = timedelta(hours=5, minutes=30)


NEW_CHANNEL_HOURS = 24


LONG_STREAK_MISSES = 20


SHORT_WINDOW = (5, 15)


CHANNEL_AGE_CAP_HOURS = 30 * 24


GAMBLE_GAP_CAP_HOURS = 7 * 24


PREDICTOR_VERSION = 3


STREAK_POOL_STRENGTH = 5.0


MAX_HIT_CANDIDATES = 5


FORECAST_HORIZON = 200


FEATURES = (
    "intercept",
    "current_streak",
    "reset_sin",
    "reset_cos",
    "session_streak",
    "new_channel",
    "after_long_streak",
    "after_long_5_to_15",
    "channel_age",
    "created_since_reset",
    "has_previous_gamble",
    "gamble_gap",
    "last_gamble_before_reset",
    "last_gamble_reset_sin",
    "last_gamble_reset_cos",
)
