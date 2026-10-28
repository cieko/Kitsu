from __future__ import annotations

import asyncio
from typing import Any

import discord
from discord.ext import commands

from app.database.repositories import ghosty_pause_repository as pause_repository
from app.services.emoji_services import get_application_emoji
from app.services.ghosty_pause_service import GhostyPauseService
from app.services.ghosty_pause_ui import GhostyPauseView
from app.services.ghosty_reaction_service import load_hit_reactions
from app.services.ghosty_service import GhostyService
from app.services.ghosty_streak_service import GhostyStreakService
from app.services.ghosty_tempchannel_service import GhostyTempChannelService

from .backfill import BackfillCommands
from .constants import logger
from .controls import DashboardControls
from .dashboard import GhostyDashboard
from .dashboard_delivery import DashboardDelivery
from .groups import ghosty, tempchannel, tempchannels
from .guards import GhostyGuards
from .live import GhostyLiveResults
from .owner_commands import OwnerCommands
from .prefix_commands import PrefixCommands
from .quota_commands import QuotaCommands
from .staff_commands import StaffCommands
from .tracking_commands import TrackingCommands


class GhostyCog(
    GhostyGuards,
    GhostyDashboard,
    DashboardDelivery,
    DashboardControls,
    GhostyLiveResults,
    TrackingCommands,
    QuotaCommands,
    StaffCommands,
    PrefixCommands,
    OwnerCommands,
    BackfillCommands,
    commands.Cog,
):
    ghosty = ghosty

    tempchannel = tempchannel

    tempchannels = tempchannels

    TEMPCHANNEL_STAFF_CHANNEL_ID = 1553860134357438484

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.service = GhostyService(bot)
        self.streak_service = GhostyStreakService(self.service)
        self.pause_service = GhostyPauseService(bot, self.service, self._pause_changed)
        self.tempchannel_service = GhostyTempChannelService(bot)
        self._initialized = False
        self._dashboard_locks: dict[int, asyncio.Lock] = {}
        self._live_result_locks: dict[tuple[int, int], asyncio.Lock] = {}
        self._background_tasks: set[asyncio.Task] = set()
        self._dashboard_refresh_tasks: dict[int, asyncio.Task] = {}
        self._dashboard_refresh_versions: dict[int, int] = {}
        self._dashboard_repost_after: dict[int, int] = {}
        self._unsaved_trackers: dict[int, int] = {}
        self._dashboard_pending_cleanup: dict[int, list[discord.Message]] = {}

    async def cog_load(self) -> None:
        load_hit_reactions()
        self._background(get_application_emoji(self.bot, "ghosty_track", ""))
        self._background(get_application_emoji(self.bot, "loading", "⏳"))
        await self.service.start()
        await self.tempchannel_service.start()
        await self.pause_service.start()
        self._initialized = True

        self.bot.add_view(self._build_dashboard_controls_view())
        for entry in await pause_repository.get_all_pauses():
            record = entry["pause"]
            if record.get("panel_id"):
                self.bot.add_view(
                    GhostyPauseView(
                        self,
                        record,
                        guild_id=int(entry["guild_id"]),
                        channel_id=int(entry["channel_id"]),
                    ),
                    message_id=int(record["panel_id"]),
                )

        if not self.refresh_dashboard.is_running():
            self.refresh_dashboard.start()

    def cog_unload(self) -> None:
        for task in tuple(self._background_tasks):
            task.cancel()
        self.service.stop()
        self.pause_service.stop()
        if self.refresh_dashboard.is_running():
            self.refresh_dashboard.cancel()

        if self.tempchannel_service.expiry_worker.is_running():
            self.tempchannel_service.expiry_worker.cancel()

    def _background(self, coroutine) -> asyncio.Task:
        task = asyncio.create_task(coroutine)
        self._background_tasks.add(task)

        def finished(done: asyncio.Task):
            self._background_tasks.discard(done)
            if not done.cancelled() and (error := done.exception()) is not None:
                logger.error(
                    "Ghosty background delivery failed.",
                    exc_info=(type(error), error, error.__traceback__),
                )

        task.add_done_callback(finished)
        return task

    async def _pause_changed(
        self, guild: discord.Guild, channel_id: int, record: dict[str, Any], active: bool
    ) -> None:
        channel = guild.get_channel(channel_id)
        if isinstance(channel, discord.TextChannel) and record.get("panel_id"):
            try:
                await channel.get_partial_message(int(record["panel_id"])).edit(
                    view=GhostyPauseView(
                        self, record, guild_id=guild.id, channel_id=channel_id, closed=not active
                    ),
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except discord.HTTPException:
                logger.warning(
                    "Could not update Ghosty pause control message in channel %s.", channel_id
                )
        await self._refresh_dashboard_for_guild(guild)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(GhostyCog(bot))
