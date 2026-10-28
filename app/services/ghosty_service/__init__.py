"""Ghosty service public interface."""

from app.database.repositories import ghosty_repository as repository
from app.services.ghosty_prediction import Context, GhostyPredictor

from .constants import GAMBLE_COMMAND_RE as GAMBLE_COMMAND_RE
from .constants import GHOSTY_BOT_ID as GHOSTY_BOT_ID
from .constants import HIT_MULTIPLIER as HIT_MULTIPLIER
from .constants import HIT_PAYOUT_RE as HIT_PAYOUT_RE
from .constants import HIT_WAGER_RE as HIT_WAGER_RE
from .constants import MISS_RE as MISS_RE
from .constants import logger as logger
from .forecast import GhostyForecasts as GhostyForecasts
from .parsing import _actor_name as _actor_name
from .parsing import _result_actor_ids as _result_actor_ids
from .parsing import classify_response as classify_response
from .parsing import classify_result as classify_result
from .parsing import detect_gamble as detect_gamble
from .parsing import parse_amount as parse_amount
from .parsing import result_belongs_to as result_belongs_to
from .parsing import verified_result_actor_id as verified_result_actor_id
from .service import GhostyService as GhostyService

__all__ = [
    "Context",
    "GAMBLE_COMMAND_RE",
    "GHOSTY_BOT_ID",
    "GhostyForecasts",
    "GhostyPredictor",
    "GhostyService",
    "HIT_MULTIPLIER",
    "HIT_PAYOUT_RE",
    "HIT_WAGER_RE",
    "MISS_RE",
    "_actor_name",
    "_result_actor_ids",
    "classify_response",
    "classify_result",
    "detect_gamble",
    "logger",
    "parse_amount",
    "repository",
    "result_belongs_to",
    "verified_result_actor_id",
]
