"""Ghosty prediction public interface."""

import numpy as np

from .constants import CHANNEL_AGE_CAP_HOURS as CHANNEL_AGE_CAP_HOURS
from .constants import FEATURES as FEATURES
from .constants import FORECAST_HORIZON as FORECAST_HORIZON
from .constants import GAMBLE_GAP_CAP_HOURS as GAMBLE_GAP_CAP_HOURS
from .constants import IST as IST
from .constants import LONG_STREAK_MISSES as LONG_STREAK_MISSES
from .constants import MAX_HIT_CANDIDATES as MAX_HIT_CANDIDATES
from .constants import NEW_CHANNEL_HOURS as NEW_CHANNEL_HOURS
from .constants import PREDICTOR_VERSION as PREDICTOR_VERSION
from .constants import RESET_OFFSET as RESET_OFFSET
from .constants import SHORT_WINDOW as SHORT_WINDOW
from .constants import STREAK_POOL_STRENGTH as STREAK_POOL_STRENGTH
from .features import Context as Context
from .features import channel_age_hours as channel_age_hours
from .features import channel_created_at as channel_created_at
from .features import feature_vector as feature_vector
from .features import first_hit_candidates as first_hit_candidates
from .features import fit_logistic as fit_logistic
from .features import logit as logit
from .features import losses as losses
from .features import rates as rates
from .features import reset_phase as reset_phase
from .features import session_day as session_day
from .features import sigmoid as sigmoid
from .features import supported_features as supported_features
from .features import utc as utc
from .model import GhostyPredictor as GhostyPredictor

__all__ = [
    "CHANNEL_AGE_CAP_HOURS",
    "Context",
    "FEATURES",
    "FORECAST_HORIZON",
    "GAMBLE_GAP_CAP_HOURS",
    "GhostyPredictor",
    "IST",
    "LONG_STREAK_MISSES",
    "MAX_HIT_CANDIDATES",
    "NEW_CHANNEL_HOURS",
    "PREDICTOR_VERSION",
    "RESET_OFFSET",
    "SHORT_WINDOW",
    "STREAK_POOL_STRENGTH",
    "channel_age_hours",
    "channel_created_at",
    "feature_vector",
    "first_hit_candidates",
    "fit_logistic",
    "logit",
    "losses",
    "np",
    "rates",
    "reset_phase",
    "session_day",
    "sigmoid",
    "supported_features",
    "utc",
]
