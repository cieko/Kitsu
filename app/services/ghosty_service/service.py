from __future__ import annotations

import asyncio
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any

import discord
from discord.ext import commands, tasks
from pymongo.errors import ConnectionFailure

from app.database.repositories import ghosty_repository as repository
from app.services.ghosty_prediction import Context, GhostyPredictor
from app.services.ghosty_training import GhostyTrainingStore

from .constants import GHOSTY_BOT_ID, logger
from .forecast import GhostyForecasts
from .parsing import (
    classify_response,
    classify_result,
    detect_gamble,
    verified_result_actor_id,
)


class GhostyService(GhostyForecasts):
    """Detect, validate, and record Ghosty gamble results."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # Serialize live updates for a channel so two simultaneous
        # results do not calculate their streak changes from the same state.
        self._channel_locks: dict[tuple[int, int], asyncio.Lock] = {}
        self._prediction_cache: dict[int, tuple[float, GhostyPredictor]] = {}
        self._prediction_locks: dict[int, asyncio.Lock] = {}
        self._prediction_versions: dict[int, int] = {}
        self._live_models: dict[int, GhostyPredictor] = {}
        self._live_contexts: dict[int, dict[int, Context]] = {}
        self._streak_forecasts: dict[tuple[int, int], dict[str, Any]] = {}
        self._saved_forecast_bases: dict[tuple[int, int], int] = {}
        self._forecast_locks: dict[tuple[int, int], asyncio.Lock] = {}
        self._pending_forecasts: dict[tuple[int, int], tuple[int, int]] = {}
        self._recent_results: dict[int, OrderedDict[int, dict[str, Any]]] = {}
        self._training_store = GhostyTrainingStore()
        self.training_worker.add_exception_type(ConnectionFailure)

    async def initialize(self) -> None:
        """Create the repository indexes."""
        await repository.initialize()

    async def start(self) -> None:
        await self.initialize()
        if not self.training_worker.is_running():
            self.training_worker.start()

    def stop(self) -> None:
        self.training_worker.cancel()

    @tasks.loop(seconds=60)
    async def training_worker(self) -> None:
        """Persist new observations and retrain even without dashboard requests."""
        for guild in self.bot.guilds:
            try:
                await self._get_prediction_model(guild.id, refresh=True)
            except Exception:
                logger.exception(
                    "Ghosty background training failed for guild %s; will retry", guild.id
                )

    @training_worker.before_loop
    async def before_training_worker(self) -> None:
        await self.bot.wait_until_ready()

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

        if source_message is not None and (
            source_message.guild is None
            or source_message.guild.id != result_message.guild.id
            or source_message.channel.id != result_message.channel.id
            or source_message.id >= result_message.id
            or detect_gamble(source_message) is None
        ):
            source_message = None

        # New response-based records use the Ghosty response ID as the unique
        # source key. This avoids collisions with existing command-based rows
        # and makes repeated processing of the same response idempotent.
        actor_id = source_message.author.id if source_message is not None else 0
        verified_actor_id = verified_result_actor_id(result_message, source_message)
        verified = verified_actor_id is not None
        if verified:
            actor_id = verified_actor_id
        return {
            **classification,
            "guild_id": result_message.guild.id,
            "channel_id": result_message.channel.id,
            "user_id": actor_id,
            "attribution_verified": verified,
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
        if classify_response(message.content) is None:
            return None
        if not await self.is_live_enabled(message.guild.id):
            return None

        source_message = None
        reference = getattr(message, "reference", None)
        resolved = getattr(reference, "resolved", None)
        if isinstance(resolved, discord.Message):
            source_message = resolved
        if source_message is None:
            for previous in reversed(getattr(self.bot, "cached_messages", ())):
                if previous.channel.id != message.channel.id or previous.id >= message.id:
                    continue
                if previous.author.id == GHOSTY_BOT_ID and classify_response(previous.content):
                    break
                if detect_gamble(previous) is not None:
                    source_message = previous
                    break

        result = await self.analyze_result_message(message, source_message)
        if result is not None and result["command_message_id"] is None:
            source_message = None
        # Normal Ghosty templates identify the player directly. Only consult
        # Discord history when both the response and local cache lack proof.
        if result is not None and not result["attribution_verified"] and source_message is None:
            try:
                async for previous in message.channel.history(limit=100, before=message):
                    if previous.author.id == GHOSTY_BOT_ID and classify_response(previous.content):
                        break
                    if detect_gamble(previous) is not None:
                        source_message = previous
                        break
            except discord.HTTPException:
                logger.exception(
                    "Could not attribute Ghosty result %s using channel history.", message.id
                )
            result = await self.analyze_result_message(message, source_message)
        if result is None:
            return None

        lock = self._get_channel_lock(message.guild.id, message.channel.id)
        async with lock:
            # The repository checks legacy IDs and deduplicates atomically;
            # repeating the same database query here adds needless latency.
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
            attribution_verified=bool(result.get("attribution_verified", False)),
        )
        # None means this gamble message was already recorded.
        if stats is None:
            return None
        if result["outcome"] == "hit":
            # Do not freeze the new streak's forecast using weights that have
            # not learned from this hit yet. Live recording remains immediate.
            self._pending_forecasts[guild_id, channel_id] = (
                int(stats.get("total_gambles", 0)),
                int(result["result_message_id"]),
            )
        recorded = {
            **result,
            "stats": stats,
            "source": source,
        }
        self._prediction_versions[guild_id] = self._prediction_versions.get(guild_id, 0) + 1
        recent = self._recent_results.setdefault(guild_id, OrderedDict())
        event = {
            "channel_id": channel_id,
            "outcome": result["outcome"],
            "created_at": result.get("created_at") or datetime.now(timezone.utc),
            "stats": {key: stats.get(key, 0) for key in ("streak", "last_streak")},
        }
        recent[int(result["result_message_id"])] = event
        if len(recent) > 2000:
            recent.popitem(last=False)
        self._observe_live_result(guild_id, event)
        return recorded

    async def get_stats(
        self,
        guild_id: int,
        channel_id: int,
    ) -> dict[str, Any]:
        """Read channel statistics; missing channels get in-memory zero defaults."""
        stats = (
            await repository.get_channel_stats(
                guild_id,
                channel_id,
            )
            or {}
        )
        result = {
            "streak": int(stats.get("streak", 0)),
            "streak_loss": int(stats.get("streak_loss", 0)),
            "net_pl": int(stats.get("net_pl", 0)),
            "hits": int(stats.get("hits", 0)),
            "misses": int(stats.get("misses", 0)),
            "total_gambles": int(stats.get("total_gambles", 0)),
        }
        if "streak_forecast" in stats:
            result["streak_forecast"] = stats["streak_forecast"]
        return result

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
