from __future__ import annotations

import asyncio
import time
from typing import Any

import discord
from discord.ext import commands

from app.database.repositories import ghosty_repository as repository
from app.services.emoji_services import get_application_emoji, get_application_emoji_url
from app.services.ghosty_reaction_service import get_hit_reaction
from app.services.ghosty_service import GHOSTY_BOT_ID
from app.services.ghosty_streak_ui import format_selected_forecast

from .constants import DEFAULT_MINIMUM_SAMPLES, HIT_SUMMARY_COLOR, TRACKER_COLOR, logger


class GhostyLiveResults:
    async def _get_streak_participant_ids(
        self,
        guild_id: int,
        channel_id: int,
        result_message_id: int,
        hit_user_id: int,
        *,
        streak_length: int | None = None,
    ) -> list[int]:
        """Collect losing users from the recorded streak that just ended."""
        if streak_length is not None and streak_length <= 0:
            return []
        history = await repository.get_gamble_history(
            guild_id,
            channel_id,
            limit=None,
        )
        participant_ids: set[int] = set()
        found_current_hit = False
        misses = 0

        for entry in reversed(history):
            if not found_current_hit:
                if str(entry.get("result_message_id")) == str(result_message_id):
                    found_current_hit = True
                continue

            # Stop at the previous hit: only the consecutive misses before the
            # current hit belong to the streak being announced.
            if entry.get("outcome") == "hit":
                break
            if entry.get("outcome") == "miss":
                if streak_length is not None and misses >= streak_length:
                    break
                misses += 1
                user_id = int(entry.get("user_id", 0))
                if user_id > 0:
                    participant_ids.add(user_id)

        participant_ids.discard(hit_user_id)
        return sorted(participant_ids)

    def _build_tracker_embed(
        self,
        recorded: dict[str, Any],
        prediction: dict[str, Any],
    ) -> discord.Embed:
        stats = recorded["stats"]
        embed = discord.Embed(
            color=TRACKER_COLOR,
            description=(
                f"**Streak: {int(stats.get('streak', 0)):,} ・ "
                f"Total Loss: {int(stats.get('streak_loss', 0)):,} Goos**\n\n"
                f"{format_selected_forecast(prediction)}"
            ),
        )
        embed.set_thumbnail(url=get_application_emoji_url("ghosty_track"))
        return embed

    async def _send_miss_tracker(self, message: discord.Message, recorded: dict[str, Any]) -> None:
        started = time.perf_counter()
        guild_id, channel_id = int(recorded["guild_id"]), int(recorded["channel_id"])
        if await repository.get_config(guild_id, f"notify:{channel_id}", "always") == "hit_only":
            return
        minimum = int(
            await repository.get_config(guild_id, "minimum_samples", DEFAULT_MINIMUM_SAMPLES)
        )
        prediction = self.service.get_live_prediction_data(
            guild_id,
            channel_id,
            stats=recorded["stats"],
            minimum_samples=minimum,
            last_gamble_at=recorded.get("created_at"),
        )
        prediction = await self.service.save_streak_prediction(
            guild_id,
            channel_id,
            stats=recorded["stats"],
            prediction=prediction,
        )
        previous_id = recorded["stats"].get("tracker_message_id")
        # Result delivery shares the channel's result lock. Finish every old
        # deletion before sending, including a replacement whose save failed.
        old_ids = {
            int(value) for value in (previous_id, self._unsaved_trackers.get(channel_id)) if value
        }
        for message_id in old_ids:
            await self._delete_previous_tracker(message.channel, message_id)
        self._unsaved_trackers.pop(channel_id, None)
        new_message = await message.channel.send(
            embed=self._build_tracker_embed(recorded, prediction),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        logger.info(
            "Ghosty tracker posted: channel=%s result=%s delivery_ms=%.1f",
            channel_id,
            message.id,
            (time.perf_counter() - started) * 1000,
        )
        self._unsaved_trackers[channel_id] = new_message.id
        try:
            await repository.set_tracker_message(guild_id, channel_id, new_message.id)
        except Exception:
            # Remove an unsaved replacement; retain its ID if deletion fails so
            # the next result cannot post another copy on top of it.
            try:
                await new_message.delete()
                self._unsaved_trackers.pop(channel_id, None)
            except discord.HTTPException:
                logger.warning("Could not remove unsaved Ghosty tracker %s.", new_message.id)
            raise
        self._unsaved_trackers.pop(channel_id, None)

    async def _delete_previous_tracker(self, channel: discord.TextChannel, message_id: int) -> None:
        try:
            await channel.get_partial_message(message_id).delete()
        except discord.NotFound:
            pass

    async def _announce_hit(self, message: discord.Message, recorded: dict[str, Any]) -> None:
        guild = message.guild
        source = message.channel
        user_id = int(recorded.get("user_id", 0))
        winner = f"<@{user_id}>" if user_id > 0 else "A player"
        stats = recorded["stats"]
        net = int(stats.get("streak_net_pl", 0))
        label = "win" if net > 0 else "loss" if net < 0 else "break-even"
        embed = discord.Embed(
            color=HIT_SUMMARY_COLOR,
            description=f"**Streak {label}:** {abs(net):,} Goos",
        )
        try:
            participants = await self._get_streak_participant_ids(
                guild.id,
                source.id,
                int(recorded["result_message_id"]),
                user_id,
                streak_length=int(stats.get("last_streak", 0)),
            )
        except Exception:
            logger.exception("Could not load participants for Ghosty hit %s.", message.id)
            participants = []
        participants = sorted(
            {participant for participant in participants if participant > 0} - {user_id}
        )
        mention_ids = sorted(set(participants) | ({user_id} if user_id > 0 else set()))
        mentions = " ".join(f"<@{participant}>" for participant in mention_ids) or None
        allowed = discord.AllowedMentions(
            users=[discord.Object(id=participant) for participant in mention_ids],
            roles=False,
            everyone=False,
            replied_user=False,
        )
        dashboard_id = await repository.get_config(guild.id, "dashboard_channel_id")
        notification = guild.get_channel(int(dashboard_id)) if dashboard_id else None
        notified = False
        if isinstance(notification, discord.TextChannel):
            try:
                contributor_mentions = " ".join(f"<@{participant}>" for participant in participants)
                notification_message = await notification.send(
                    content=(
                        f"{source.mention}{' ' + contributor_mentions if contributor_mentions else ''}\n"
                        f"🎉  **A Ghosty hit has been recorded!** Kudos to {winner}\n"
                        f"Total streak: {int(stats.get('last_streak', 0)):,} ・ Total profit: {net:,} Goos\n"
                        f"-# [Jump to message]({message.jump_url})"
                    ),
                    allowed_mentions=allowed,
                    suppress_embeds=True,
                )
                notified = True
                self._queue_dashboard_refresh(guild, after_message_id=notification_message.id)
            except discord.HTTPException:
                logger.exception("Could not post Ghosty hit summary in the dashboard channel.")

        if notification is not None and notification.id == source.id:
            # The market channel uses one plain-text notification, including
            # when a gamble happened in the market channel itself.
            await self._learn_next_streak(guild, source.id)
            return

        reaction = get_hit_reaction(net_result=net)
        summary = f"{embed.description}\n{discord.utils.escape_markdown(reaction)}\n\n"
        loading = await get_application_emoji(self.bot, "loading", "⏳")
        embed.description = f"{summary}{loading} Learning from this hit…"
        announcement = await source.send(
            content=None if notified else mentions,
            embed=embed,
            allowed_mentions=discord.AllowedMentions.none() if notified else allowed,
        )
        self._background(
            self._update_hit_estimate(
                announcement,
                guild,
                source.id,
                int(stats.get("total_gambles", 0)),
                embed,
                summary,
            )
        )

    async def _learn_next_streak(self, guild: discord.Guild, channel_id: int) -> dict[str, Any]:
        minimum = int(
            await repository.get_config(guild.id, "minimum_samples", DEFAULT_MINIMUM_SAMPLES)
        )
        prediction = await self.service.get_prediction_data(
            guild.id,
            channel_id,
            minimum_samples=minimum,
            refresh_model=True,
        )
        self._queue_dashboard_refresh(guild)
        return prediction

    async def _update_hit_estimate(
        self,
        announcement: discord.Message,
        guild: discord.Guild,
        channel_id: int,
        streak_base: int,
        embed: discord.Embed,
        summary: str,
    ) -> None:
        """Keep the reaction intact while replacing just the loading estimate."""
        next_hit = "Unavailable"
        try:
            prediction = await self._learn_next_streak(guild, channel_id)
            forecast = prediction.get("streak_forecast")
            current_base = prediction.get(
                "streak_base", forecast["streak_base"] if forecast else streak_base
            )
            hit_at = prediction.get("hit_at") if prediction.get("ready") else None
            if current_base != streak_base:
                next_hit = "Streak already ended"
            else:
                next_hit = f"{hit_at:,}" if hit_at is not None else "Still learning"
        except Exception:
            logger.exception("Could not learn the next Ghosty estimate for channel %s.", channel_id)
        updated = embed.copy()
        updated.description = f"{summary}**Next Estimated Hit @** `{next_hit}`"
        try:
            await announcement.edit(embed=updated, allowed_mentions=discord.AllowedMentions.none())
        except discord.NotFound:
            pass
        except discord.HTTPException:
            logger.warning(
                "Could not update the next Ghosty estimate on message %s.", announcement.id
            )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None:
            return
        if self.bot.user is not None and message.author.id == self.bot.user.id:
            return
        guild = message.guild
        if message.author.id == GHOSTY_BOT_ID:
            key = (guild.id, message.channel.id)
            lock = self._live_result_locks.setdefault(key, asyncio.Lock())
            # Include recording and tracker replacement in the channel lock so
            # simultaneous replies cannot leave older values as the latest card.
            async with lock:
                started = time.perf_counter()
                try:
                    recorded = await self.service.process_message(message)
                except Exception:
                    logger.exception("Ghosty result processing failed for message %s.", message.id)
                    return
                if recorded is None:
                    return
                logger.info(
                    "Ghosty result recorded: channel=%s result=%s recording_ms=%.1f",
                    message.channel.id,
                    message.id,
                    (time.perf_counter() - started) * 1000,
                )
                self._queue_dashboard_refresh(guild)
                try:
                    if recorded["outcome"] == "hit":
                        self._background(self._announce_hit(message, recorded))
                    else:
                        await self._send_miss_tracker(message, recorded)
                except Exception:
                    logger.exception(
                        "Ghosty result notification failed for message %s.", message.id
                    )
            return

        dashboard_id = await repository.get_config(guild.id, "dashboard_channel_id")
        if dashboard_id and str(message.channel.id) == str(dashboard_id):
            self._queue_dashboard_refresh(guild, after_message_id=message.id)
