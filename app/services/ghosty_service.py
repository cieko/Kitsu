from __future__ import annotations
import asyncio
import logging
import re
from datetime import datetime
from typing import Any
import discord
from discord.ext import commands
from app.database.repositories import ghosty_repository as repository
logger = logging.getLogger(__name__)
GHOSTY_BOT_ID = 458302301187342336
HIT_MULTIPLIER = 10
# Supports ?give ghosty 12, ?send ghosty 1000,
# ?send @ghosty 1000, and Discord mentions of Ghosty.
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


class GhostyService:
    """Detect, validate, and record Ghosty gamble results."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # Serialize live updates for a channel so two simultaneous
        # results do not calculate their streak changes from the same state.
        self._channel_locks: dict[tuple[int, int], asyncio.Lock] = {}

    async def initialize(self) -> None:
        """Create the repository indexes."""
        await repository.initialize()

    def _get_channel_lock(
        self,
        guild_id: int,
        channel_id: int,
    ) -> asyncio.Lock:
        key = (guild_id, channel_id)
        if key not in self._channel_locks:
            self._channel_locks[key] = asyncio.Lock()
        return self._channel_locks[key]

    async def is_live_enabled(self, guild_id: int) -> bool:
        """Live tracking remains off unless explicitly enabled."""
        return bool(
            await repository.get_config(
                guild_id,
                "live_enabled",
                default=False,
            )
        )

    async def analyze_messages(
        self,
        source_message: discord.Message,
        result_message: discord.Message,
    ) -> dict[str, Any] | None:
        """Validate a gamble and a normal Ghosty result message."""
        if source_message.guild is None or result_message.guild is None:
            return None
        if source_message.guild.id != result_message.guild.id:
            return None
        if source_message.channel.id != result_message.channel.id:
            return None
        # A result must occur after its gamble command.
        if result_message.id <= source_message.id:
            return None
        if result_message.author.id != GHOSTY_BOT_ID:
            return None
        wager = detect_gamble(source_message)
        if wager is None:
            return None
        classification = classify_result(
            wager,
            result_message.content,
        )
        if classification is None:
            return None
        return {
            **classification,
            "guild_id": source_message.guild.id,
            "channel_id": source_message.channel.id,
            "user_id": source_message.author.id,
            "source_message_id": source_message.id,
            "result_message_id": result_message.id,
            "created_at": source_message.created_at,
        }

    async def analyze_result_message(
        self,
        result_message: discord.Message,
        source_message: discord.Message | None = None,
    ) -> dict[str, Any] | None:
        """Classify a Ghosty result independently of command wager matching.

        A nearby command is used only to attribute the result to a user. It is
        never used to decide whether the response is a hit or miss.
        """
        if result_message.guild is None or result_message.author.id != GHOSTY_BOT_ID:
            return None

        classification = classify_response(result_message.content)
        if classification is None:
            return None

        if (
            source_message is not None
            and (
                source_message.guild is None
                or source_message.guild.id != result_message.guild.id
                or source_message.channel.id != result_message.channel.id
                or source_message.id >= result_message.id
                or detect_gamble(source_message) is None
            )
        ):
            source_message = None

        # New response-based records use the Ghosty response ID as the unique
        # source key. This avoids collisions with existing command-based rows
        # and makes repeated processing of the same response idempotent.
        return {
            **classification,
            "guild_id": result_message.guild.id,
            "channel_id": result_message.channel.id,
            "user_id": source_message.author.id if source_message is not None else 0,
            "source_message_id": result_message.id,
            "result_message_id": result_message.id,
            "command_message_id": source_message.id if source_message is not None else None,
            "created_at": result_message.created_at,
        }

    async def process_message(
        self,
        message: discord.Message,
    ) -> dict[str, Any] | None:
        """Record each valid Ghosty response independently."""
        if message.author.id != GHOSTY_BOT_ID or message.guild is None:
            return None
        if not await self.is_live_enabled(message.guild.id):
            return None

        # Backwards compatibility: a response might already be recorded under
        # its original command ID by the previous implementation.
        existing = await repository.get_gamble_by_result_message(
            message.guild.id,
            message.id,
        )
        if existing is not None:
            return None

        # Use the most recent supported command only for user attribution. Do
        # not compare its wager with the response and do not require a match.
        source_message = None
        try:
            async for previous in message.channel.history(limit=100, before=message):
                # Ignore non-gambling Ghosty messages such as balance replies.
                # A prior valid gambling response ends the attribution window.
                if previous.author.id == GHOSTY_BOT_ID and classify_response(previous.content):
                    break
                if detect_gamble(previous) is not None:
                    source_message = previous
                    break
        except (discord.Forbidden, discord.HTTPException):
            logger.exception(
                "Could not scan preceding messages for Ghosty result %s.",
                message.id,
            )

        result = await self.analyze_result_message(message, source_message)
        if result is None:
            return None

        lock = self._get_channel_lock(message.guild.id, message.channel.id)
        async with lock:
            # Recheck after acquiring the per-channel lock.
            existing = await repository.get_gamble_by_result_message(
                message.guild.id,
                message.id,
            )
            if existing is not None:
                return None
            return await self.record_result(result, source="live")

    async def record_result(
        self,
        result: dict[str, Any],
        *,
        source: str = "live",
    ) -> dict[str, Any] | None:
        guild_id = int(result["guild_id"])
        channel_id = int(result["channel_id"])
        user_id = int(result["user_id"])
        stats = await repository.record_result_atomically(
            guild_id=guild_id,
            channel_id=channel_id,
            user_id=user_id,
            source_message_id=int(result["source_message_id"]),
            result_message_id=int(result["result_message_id"]),
            command_message_id=(
                int(result["command_message_id"])
                if result.get("command_message_id") is not None
                else None
            ),
            wager=int(result["wager"]),
            outcome=str(result["outcome"]),
            payout=int(result["payout"]),
            source=source,
            created_at=result.get("created_at"),
        )
        # None means this gamble message was already recorded.
        if stats is None:
            return None
        return {
            **result,
            "stats": stats,
            "source": source,
        }

    async def get_stats(
        self,
        guild_id: int,
        channel_id: int,
    ) -> dict[str, Any]:
        """Return combined channel statistics, including empty defaults."""
        stats = await repository.ensure_channel_stats(
            guild_id,
            channel_id,
        )
        return {
            "streak": int(stats.get("streak", 0)),
            "streak_loss": int(stats.get("streak_loss", 0)),
            "net_pl": int(stats.get("net_pl", 0)),
            "hits": int(stats.get("hits", 0)),
            "misses": int(stats.get("misses", 0)),
            "total_gambles": int(stats.get("total_gambles", 0)),
        }

    async def set_live_enabled(
        self,
        guild_id: int,
        enabled: bool,
    ) -> None:
        """Explicitly enable or disable live tracking."""
        await repository.set_config(
            guild_id,
            "live_enabled",
            enabled,
        )

    async def get_prediction_data(
        self,
        guild_id: int,
        channel_id: int,
        *,
        minimum_samples: int = 50,
    ) -> dict[str, Any]:
        """Return a basic historical hit-rate estimate.
        This deliberately does not claim that a losing streak makes the
        next gamble more likely to hit. A streak alone cannot establish that.
        """
        counts = await repository.get_gamble_counts(
            guild_id,
            channel_id,
        )
        total = counts["total"]
        hits = counts["hits"]
        if total < minimum_samples:
            return {
                "ready": False,
                "samples": total,
                "minimum_samples": minimum_samples,
                "hit_percent": None,
                "hit_at": None,
            }
        if total == 0:
            return {
                "ready": False,
                "samples": 0,
                "minimum_samples": minimum_samples,
                "hit_percent": None,
                "hit_at": None,
            }
        hit_rate = hits / total
        # No estimate when the historical sample contains no hits.
        if hit_rate <= 0:
            hit_at = None
        else:
            stats = await self.get_stats(guild_id, channel_id)
            # This is a rough historical expectation, not a prediction
            # that a hit is due at a particular streak length.
            hit_at = stats["streak"] + round(1 / hit_rate)
        return {
            "ready": True,
            "samples": total,
            "minimum_samples": minimum_samples,
            "hit_percent": round(hit_rate * 100, 2),
            "hit_at": hit_at,
        }
