from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)


GHOSTY_BOT_ID = 458302301187342336


HIT_MULTIPLIER = 10


GAMBLE_COMMAND_RE = re.compile(
    r"^\s*\??\s*(?:give|send)\s+"
    r"(?:<@!?458302301187342336>|@?ghosty)"
    r"\s+([\d,]+)\b",
    re.IGNORECASE,
)


MISS_RE = re.compile(
    r"successfully\s+sent\s+(?:\*\*)?\s*([\d,]+)\s*(?:\*\*)?"
    r"\s+goos?\b",
    re.IGNORECASE,
)


HIT_PAYOUT_RE = re.compile(
    r"(?:going\s+to\s+get|you(?:'|’)ll\s+get)"
    r"\s+(?:\*\*)?\s*([\d,]+)\s*(?:\*\*)?\s+goos?\b",
    re.IGNORECASE,
)


HIT_WAGER_RE = re.compile(
    r"want\s+to\s+send\s+(?:\*\*)?\s*([\d,]+)\s*(?:\*\*)?"
    r"\s+goos?\b",
    re.IGNORECASE,
)
