from __future__ import annotations

import logging
import re

import discord

logger = logging.getLogger(__name__)


STEAL_TIMEOUT = 300


ASSET_COLOR = discord.Colour(0x7DBBEB)


CDN_EMOJI_URL = "https://cdn.discordapp.com/emojis"


CUSTOM_EMOJI_PATTERN = re.compile(
    r"<(?P<animated>a?):(?P<name>[A-Za-z0-9_]{2,32}):(?P<id>\d{15,25})>"
)


FORWARDED_EMOJI_PATTERN = re.compile(
    r"\[:(?P<name>[A-Za-z0-9_]{2,32}):\]"
    r"\(\s*<?(?P<url>https?://cdn\.discordapp\.com/emojis/"
    r"(?P<id>\d{15,25})\.(?P<ext>png|gif|webp|jpeg|jpg)"
    r"(?:\?[^)\s>]*)?)>?\s*\)",
    re.IGNORECASE,
)


RAW_EMOJI_URL_PATTERN = re.compile(
    r"https?://cdn\.discordapp\.com/emojis/"
    r"(?P<id>\d{15,25})\.(?P<ext>png|gif|webp|jpeg|jpg)"
    r"(?:\?[^\s<>]*)?",
    re.IGNORECASE,
)
