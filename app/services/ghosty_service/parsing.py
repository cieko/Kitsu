from __future__ import annotations

import re
import unicodedata
from typing import Any

import discord

from .constants import (
    GAMBLE_COMMAND_RE,
    GHOSTY_BOT_ID,
    HIT_MULTIPLIER,
    HIT_PAYOUT_RE,
    HIT_WAGER_RE,
    MISS_RE,
)


def _result_actor_ids(content: str) -> set[int]:
    header = re.split(r"\byou(?:\b|['’])", content, maxsplit=1, flags=re.IGNORECASE)[0]
    return {
        int(value) for value in re.findall(r"<@!?(\d+)>", header) if int(value) != GHOSTY_BOT_ID
    }


def _actor_name(value: str) -> str:
    value = re.sub(r"<a?:\w+:\d+>", "", value)
    value = "".join(
        char for char in unicodedata.normalize("NFKC", value) if unicodedata.category(char) != "Cf"
    )
    return " ".join(value.split()).strip().casefold()


def verified_result_actor_id(
    message: discord.Message,
    source_message: discord.Message | None = None,
) -> int | None:
    """Resolve the player named or directly referenced by Ghosty's response."""
    if (
        message.guild is None
        or message.author.id != GHOSTY_BOT_ID
        or classify_response(message.content) is None
    ):
        return None
    mentioned = _result_actor_ids(message.content)
    if mentioned:
        return next(iter(mentioned)) if len(mentioned) == 1 else None
    reference = getattr(message, "reference", None)
    if reference and source_message is not None:
        if (
            reference.message_id == source_message.id
            and source_message.guild is not None
            and source_message.guild.id == message.guild.id
            and source_message.channel.id == message.channel.id
            and source_message.id < message.id
            and detect_gamble(source_message) is not None
        ):
            return source_message.author.id
    # Ghosty's normal template bolds the player's display name. Only trust a
    # unique name when the full guild member cache is available.
    if not getattr(message.guild, "chunked", False):
        return None
    header = re.split(r"\byou(?:\b|['’])", message.content, maxsplit=1, flags=re.IGNORECASE)[0]
    name = re.search(r"\*\*(.+?)\*\*", header)
    if name is None:
        return None
    expected = _actor_name(name.group(1))
    matches = {
        candidate.id
        for candidate in message.guild.members
        if not candidate.bot
        and expected in {_actor_name(candidate.display_name), _actor_name(candidate.name)}
    }
    return next(iter(matches)) if len(matches) == 1 else None


def result_belongs_to(
    message: discord.Message,
    member: discord.Member,
    source_message: discord.Message | None = None,
) -> bool:
    """Verify a response's player independently of nearest-command attribution."""
    return verified_result_actor_id(message, source_message) == member.id


def parse_amount(value: str) -> int:
    """Convert an amount such as '1,000' to an integer."""
    return int(value.replace(",", ""))


def detect_gamble(message: discord.Message) -> int | None:
    """Return the wager if the message is a supported Ghosty gamble."""
    if message.guild is None or message.author.bot:
        return None
    match = GAMBLE_COMMAND_RE.match(message.content)
    if not match:
        return None
    try:
        wager = parse_amount(match.group(1))
    except ValueError:
        return None
    return wager if wager > 0 else None


def classify_result(
    wager: int,
    response_content: str,
) -> dict[str, Any] | None:
    """Classify a Ghosty response only when the amount is consistent."""
    if wager <= 0:
        return None
    hit_payout_match = HIT_PAYOUT_RE.search(response_content)
    if hit_payout_match:
        hit_wager_match = HIT_WAGER_RE.search(response_content)
        if not hit_wager_match:
            return None
        try:
            response_wager = parse_amount(hit_wager_match.group(1))
            payout = parse_amount(hit_payout_match.group(1))
        except ValueError:
            return None
        if response_wager != wager:
            return None
        if payout != wager * HIT_MULTIPLIER:
            return None
        return {
            "outcome": "hit",
            "wager": wager,
            "payout": payout,
            "net_pl_delta": payout,
        }
    miss_match = MISS_RE.search(response_content)
    if miss_match:
        try:
            transferred = parse_amount(miss_match.group(1))
        except ValueError:
            return None
        if transferred != wager:
            return None
        return {
            "outcome": "miss",
            "wager": wager,
            "payout": 0,
            "net_pl_delta": -wager,
        }
    # Ambiguous response: do not record it.
    return None


def classify_response(response_content: str) -> dict[str, Any] | None:
    """Classify a valid Ghosty gambling response without matching a command.

    Ghosty's response text contains the actual wager for both hits and misses.
    Balance replies and other unrelated messages do not match these patterns.
    """
    hit_payout_match = HIT_PAYOUT_RE.search(response_content)
    if hit_payout_match:
        hit_wager_match = HIT_WAGER_RE.search(response_content)
        if hit_wager_match is None:
            return None
        try:
            wager = parse_amount(hit_wager_match.group(1))
            payout = parse_amount(hit_payout_match.group(1))
        except ValueError:
            return None
        if wager <= 0 or payout != wager * HIT_MULTIPLIER:
            return None
        return {
            "outcome": "hit",
            "wager": wager,
            "payout": payout,
            "net_pl_delta": payout,
        }

    miss_match = MISS_RE.search(response_content)
    if miss_match:
        try:
            wager = parse_amount(miss_match.group(1))
        except ValueError:
            return None
        if wager <= 0:
            return None
        return {
            "outcome": "miss",
            "wager": wager,
            "payout": 0,
            "net_pl_delta": -wager,
        }

    return None
