from __future__ import annotations

import discord
from discord import app_commands

from app.database.repositories import ghosty_repository as repository
from app.services.ghosty_service import GHOSTY_BOT_ID, detect_gamble

from .constants import DEFAULT_MINIMUM_SAMPLES, logger
from .groups import ghosty


class TrackingCommands:
    @ghosty.command(
        name="setup",
        description="Choose the channel for the Ghosty dashboard.",
    )
    @app_commands.default_permissions(manage_guild=True)
    async def setup_dashboard(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
    ) -> None:
        if not await self._require_admin(interaction):
            return

        guild = interaction.guild
        if guild is None:
            return

        await interaction.response.defer(
            ephemeral=True,
            thinking=True,
        )

        try:
            async with self._dashboard_lock(guild.id):
                old_channel_id = await repository.get_config(guild.id, "dashboard_channel_id")
                old_channel = guild.get_channel(int(old_channel_id)) if old_channel_id else None
                old_messages = (
                    await self._get_dashboard_messages(guild, old_channel)
                    if isinstance(old_channel, discord.TextChannel)
                    else []
                )
                await self._post_dashboard_stack(guild, channel, old_messages)

        except discord.HTTPException:
            logger.exception(
                "Failed to set up Ghosty dashboard stack in guild %s.",
                guild.id,
            )

            await interaction.followup.send(
                "I couldn't post all three dashboard messages there. Check that "
                "I have View Channel, Send Messages, and Read Message History "
                "permissions. Run `/ghosty setup` again after fixing permissions.",
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            f"Ghosty dashboard and controls configured in {channel.mention}.",
            ephemeral=True,
        )

    @ghosty.command(
        name="test",
        description="Inspect a gamble message without recording it.",
    )
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(
        message_id="ID of the original gamble command message.",
    )
    async def test_gamble(
        self,
        interaction: discord.Interaction,
        message_id: str,
    ) -> None:
        if not await self._require_admin(interaction):
            return

        if not message_id.isdigit():
            await interaction.response.send_message(
                "Message ID must contain digits only.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(
            ephemeral=True,
            thinking=True,
        )

        source = await self._get_message(
            interaction.channel,
            int(message_id),
        )

        if source is None:
            await interaction.followup.send(
                "I couldn't fetch that message. Check the ID, channel, "
                "and my message-history permissions.",
                ephemeral=True,
            )
            return

        wager = detect_gamble(source)

        if wager is None:
            await interaction.followup.send(
                "This message doesn't match a supported Ghosty wager command.",
                ephemeral=True,
            )
            return

        matched_result = None

        try:
            async for candidate in interaction.channel.history(
                limit=1000,
                after=source,
                oldest_first=True,
            ):
                if candidate.author.id != GHOSTY_BOT_ID:
                    continue

                result = await self.service.analyze_result_message(candidate, source)
                if result is not None:
                    matched_result = (result, candidate)
                    break

        except (discord.Forbidden, discord.HTTPException):
            await interaction.followup.send(
                f"Detected wager command: **{wager:,} Goos**.\n"
                "I couldn't scan the messages after it. Check my View Channel "
                "and Read Message History permissions.",
                ephemeral=True,
            )
            return

        if matched_result is None:
            await interaction.followup.send(
                f"Detected wager command: **{wager:,} Goos**.\n"
                "No valid Ghosty gambling response was found after this message. "
                "Other Ghosty messages, including balance replies, are ignored. "
                "No stats were changed.",
                ephemeral=True,
            )
            return

        result, response = matched_result
        await interaction.followup.send(
            f"**Response wager:** {result['wager']:,} Goos\n"
            f"**Ghosty message:** [Jump to message]({response.jump_url})\n"
            f"**Classification:** {result['outcome'].upper()}\n"
            f"**Payout:** {result['payout']:,} Goos\n"
            "**Database:** unchanged",
            ephemeral=True,
        )

    @ghosty.command(
        name="config",
        description="Configure the minimum history required for predictions.",
    )
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(
        minimum_samples="Minimum recorded gambles per channel (1–100000).",
        pattern_predictions="Consider learned daily reset, new-channel, and streak patterns when validated.",
    )
    async def configure(
        self,
        interaction: discord.Interaction,
        minimum_samples: app_commands.Range[int, 1, 100000] | None = None,
        pattern_predictions: bool | None = None,
    ) -> None:
        if not await self._require_admin(interaction):
            return

        guild_id = interaction.guild.id
        if minimum_samples is not None:
            await repository.set_config(guild_id, "minimum_samples", int(minimum_samples))
        if pattern_predictions is not None:
            await repository.set_config(guild_id, "prediction_patterns", pattern_predictions)
        self.service.invalidate_prediction_cache(guild_id)
        threshold = await repository.get_config(
            guild_id, "minimum_samples", DEFAULT_MINIMUM_SAMPLES
        )
        enabled = await repository.get_config(guild_id, "prediction_patterns", True)

        await interaction.response.send_message(
            f"Prediction threshold: **{threshold} gambles per channel**.\n"
            f"Pattern predictions: **{'enabled when validated' if enabled else 'disabled; historical rates only'}**.",
            ephemeral=True,
        )

    @ghosty.command(
        name="start",
        description="Enable live Ghosty tracking in this server.",
    )
    @app_commands.default_permissions(manage_guild=True)
    async def start_tracking(
        self,
        interaction: discord.Interaction,
    ) -> None:
        if not await self._require_admin(interaction):
            return

        await self.service.set_live_enabled(
            interaction.guild.id,
            True,
        )

        await interaction.response.send_message(
            "Live Ghosty tracking is now enabled. Kitsu will attempt to "
            "match ordinary Ghosty result messages to preceding gamble "
            "commands. Historical backfill is separate.",
            ephemeral=True,
        )

    @ghosty.command(
        name="notify",
        description="Choose whether this channel displays missed gambles.",
    )
    @app_commands.describe(
        mode="always shows misses; hit_only announces hits only.",
    )
    @app_commands.choices(
        mode=[
            app_commands.Choice(name="always", value="always"),
            app_commands.Choice(name="hit_only", value="hit_only"),
        ]
    )
    async def notify_mode(
        self,
        interaction: discord.Interaction,
        mode: app_commands.Choice[str],
    ) -> None:
        if not await self._require_guild(interaction):
            return

        await repository.set_config(
            interaction.guild.id,
            f"notify:{interaction.channel_id}",
            mode.value,
        )

        await interaction.response.send_message(
            f"Ghosty notifications for this channel are now **{mode.value}**.",
            ephemeral=True,
        )

    @ghosty.command(
        name="stats",
        description="Show combined Ghosty statistics for this channel.",
    )
    async def show_stats(
        self,
        interaction: discord.Interaction,
    ) -> None:
        if not await self._require_guild(interaction):
            return

        stats = await self.service.get_stats(
            interaction.guild.id,
            interaction.channel_id,
        )

        net_pl = stats["net_pl"]

        await interaction.response.send_message(
            "**Ghosty Channel Stats**\n"
            f"Streak: **{stats['streak']}**\n"
            f"Current Streak Loss: **{stats['streak_loss']:,} Goos**\n"
            f"Cumulative Net P/L: **{net_pl:+,} Goos**\n"
            f"Hits: **{stats['hits']:,}** ・ "
            f"Misses: **{stats['misses']:,}** ・ "
            f"Total: **{stats['total_gambles']:,}**",
            ephemeral=True,
        )
