from __future__ import annotations

import re

CUSTOM_EMOJI_PATTERN = re.compile(r"<a?:[A-Za-z0-9_]+:\d+>")


DEFAULT_GIF_DOMAINS = (
    "tenor.com",
    "media.tenor.com",
    "giphy.com",
    "media.giphy.com",
    "i.giphy.com",
)
