from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


DEFAULT_CATEGORY_ID = 1562820882794287254


EXPIRY_CHECK_SECONDS = 30


WARNING_MINUTES = 15


MAX_LIFETIME_SECONDS = 7 * 24 * 60 * 60


DURATION_PRESETS: dict[str, int] = {
    "1h": 60 * 60,
    "2h": 2 * 60 * 60,
    "6h": 6 * 60 * 60,
    "12h": 12 * 60 * 60,
    "1d": 24 * 60 * 60,
    "3d": 3 * 24 * 60 * 60,
    "7d": 7 * 24 * 60 * 60,
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class TempChannelError(Exception):
    """Expected temporary-channel operation failure."""
