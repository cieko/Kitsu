from __future__ import annotations

import asyncio

import discord
from discord import app_commands
from discord.ext import commands, tasks
from pymongo.errors import ConnectionFailure

from app.database.repositories import sticky_repository as repository
from app.services.sticky_service import StickyService

from .constants import logger
from .operations import StickyOperations
from .views import StickyListView


@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
class Sticky(StickyOperations, commands.GroupCog, group_name="sticky"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.sticky_service = StickyService()
        self._channel_locks: dict[int, asyncio.Lock] = {}
        self._unsaved_messages: dict[int, int] = {}
        self.reconcile_stickies.add_exception_type(ConnectionFailure)

    async def cog_load(self):
        self.reconcile_stickies.start()

    def cog_unload(self):
        self.reconcile_stickies.cancel()

    @tasks.loop(seconds=30)
    async def reconcile_stickies(self):
        """Repair missed events, deleted stickies, and interrupted cleanup."""
        guild_id = getattr(self.bot, "main_guild_id", None)
        if not guild_id:
            return
        for configured in await repository.get_stickies(guild_id):
            channel_id = int(configured["channel_id"])
            channel = self.bot.get_channel(channel_id)
            if not isinstance(channel, discord.TextChannel) or channel.guild.id != guild_id:
                continue
            try:
                async with self._lock(channel_id):
                    sticky = await repository.get_sticky(guild_id, channel_id)
                    if sticky is None:
                        continue
                    await self._cleanup_pending(channel, sticky)
                    if not sticky.get("active", True):
                        continue
                    current = await self._find_current(channel, sticky)
                    if current is None:
                        await self._replace_locked(channel, sticky)
                        continue
                    async for recent in channel.history(
                        limit=100, after=discord.Object(id=current.id), oldest_first=False
                    ):
                        if self.bot.user and recent.author.id == self.bot.user.id:
                            continue
                        if recent.author.bot and sticky.get("ignore_bots", True):
                            continue
                        await self._replace_locked(channel, sticky, trigger_id=recent.id)
                        break
            except Exception:
                logger.exception(
                    "Could not reconcile sticky in channel %s; will retry.", channel_id
                )

    @reconcile_stickies.before_loop
    async def before_reconcile(self):
        await self.bot.wait_until_ready()

    async def interaction_check(
        self,
        interaction: discord.Interaction,
    ) -> bool:
        if interaction.guild is None:
            await interaction.response.send_message(
                "Sticky commands can only be used inside a server.",
                ephemeral=True,
            )
            return False

        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "Unable to verify your server permissions.",
                ephemeral=True,
            )
            return False

        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "You need Administrator permission to manage sticky messages.",
                ephemeral=True,
            )
            return False

        return True

    @app_commands.command(
        name="add",
        description="Add or replace a channel's sticky message.",
    )
    @app_commands.describe(
        channel="The channel where the sticky should appear.",
        message="Text or an embed script.",
    )
    async def add(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        message: str,
    ):
        guild = interaction.guild
        if guild is None:
            return

        await interaction.response.defer(ephemeral=True)
        try:
            await self.configure_sticky(channel, message, interaction.user.id)
            await interaction.followup.send(
                f"Sticky message configured for {channel.mention}.",
                ephemeral=True,
            )
        except Exception as exc:
            await self.report_error(interaction, exc)

    @app_commands.command(
        name="list",
        description="List and configure sticky messages.",
    )
    async def list_stickies(
        self,
        interaction: discord.Interaction,
    ):
        guild = interaction.guild
        if guild is None:
            return

        await interaction.response.defer(ephemeral=True)
        try:
            stickies = await repository.get_stickies(guild.id)
            view = StickyListView(guild, stickies, cog=self)
            await interaction.followup.send(view=view, ephemeral=True)
        except Exception as exc:
            await self.report_error(interaction, exc)

    @app_commands.command(
        name="edit",
        description="Edit a channel's sticky message.",
    )
    @app_commands.describe(
        channel="The channel whose sticky you want to edit.",
        message="The new text or embed script.",
    )
    async def edit(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        message: str,
    ):
        guild = interaction.guild
        if guild is None:
            return

        await interaction.response.defer(ephemeral=True)
        try:
            await self.configure_sticky(
                channel, message, interaction.user.id, require_existing=True
            )
            await interaction.followup.send(
                f"Sticky message updated for {channel.mention}.",
                ephemeral=True,
            )
        except Exception as exc:
            await self.report_error(interaction, exc)

    @app_commands.command(
        name="remove",
        description="Remove a channel's sticky message.",
    )
    @app_commands.describe(
        channel="The channel whose sticky you want to remove.",
    )
    async def remove(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
    ):
        guild = interaction.guild
        if guild is None:
            return

        await interaction.response.defer(ephemeral=True)
        try:
            removed = await self.remove_sticky(guild, channel.id, channel)
            await interaction.followup.send(
                f"Sticky message removed from {channel.mention}."
                if removed
                else f"There is no sticky configured for {channel.mention}.",
                ephemeral=True,
            )
        except Exception as exc:
            await self.report_error(interaction, exc)

    @app_commands.command(
        name="toggle",
        description="Enable or pause a channel's sticky message.",
    )
    @app_commands.describe(
        channel="The channel whose sticky you want to toggle.",
        enabled="Whether the sticky should be enabled.",
    )
    async def toggle(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        enabled: bool,
    ):
        guild = interaction.guild
        if guild is None:
            return

        await interaction.response.defer(ephemeral=True)
        try:
            async with self._lock(channel.id):
                sticky = await repository.get_sticky(guild.id, channel.id)
                if sticky is None:
                    raise ValueError(f"There is no sticky configured for {channel.mention}.")
                if enabled:
                    self._validate(channel, sticky["message_script"])
                await repository.set_sticky_active(guild.id, channel.id, enabled)
                if enabled:
                    await self._replace_locked(channel, sticky)
            state = "enabled" if enabled else "paused"
            await interaction.followup.send(
                f"Sticky message {state} for {channel.mention}.", ephemeral=True
            )
        except Exception as exc:
            await self.report_error(interaction, exc)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None:
            return

        if not isinstance(message.channel, discord.TextChannel):
            return

        # Kitsu must never trigger its own sticky repost.
        if self.bot.user and message.author.id == self.bot.user.id:
            return

        main_guild_id = getattr(self.bot, "main_guild_id", None)

        if main_guild_id and message.guild.id != main_guild_id:
            return

        try:
            async with self._lock(message.channel.id):
                sticky = await repository.get_sticky(message.guild.id, message.channel.id)
                if not sticky or not sticky.get("active", True):
                    return
                if message.author.bot and sticky.get("ignore_bots", True):
                    return
                await self._replace_locked(message.channel, sticky, trigger_id=message.id)
        except Exception:
            logger.exception(
                "Could not repost sticky in channel %s; reconciliation will retry.",
                message.channel.id,
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(Sticky(bot))
