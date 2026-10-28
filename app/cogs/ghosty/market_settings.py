from __future__ import annotations

import discord

from app.database.repositories import ghosty_repository as repository

from .market_settings_page import build_settings_page


async def show_settings(
    self,
    interaction: discord.Interaction,
) -> None:
    guild = interaction.guild

    if guild is None:
        await interaction.response.send_message(
            "Use these settings inside a server.",
            ephemeral=True,
        )
        return

    permissions = interaction.user.guild_permissions

    if not (permissions.administrator or permissions.manage_guild):
        await interaction.response.send_message(
            "You need Manage Server permission to change Ghosty dashboard settings.",
            ephemeral=True,
        )
        return

    # Only include ordinary text channels that the bot can view.
    bot_member = guild.get_member(self.bot.user.id) if self.bot.user is not None else None

    channels = [
        channel
        for channel in guild.text_channels
        if (bot_member is None or channel.permissions_for(bot_member).view_channel)
    ]

    channels.sort(
        key=lambda channel: (
            channel.position,
            channel.name.casefold(),
        )
    )

    accessible_channel_ids = {channel.id for channel in channels}

    existing_exclusions = await repository.get_config(
        guild.id,
        "dashboard_excluded_channels",
        [],
    )

    excluded_channel_ids: set[int] = {int(channel_id) for channel_id in existing_exclusions}

    # Preserve exclusions for channels that are no longer accessible.
    excluded_channel_ids.intersection_update(excluded_channel_ids - accessible_channel_ids)

    # Keep the current exclusions for channels in this list.
    excluded_channel_ids.update(
        int(channel_id)
        for channel_id in existing_exclusions
        if int(channel_id) in accessible_channel_ids
    )

    channels_per_page = 10

    total_pages = max(
        1,
        (len(channels) + channels_per_page - 1) // channels_per_page,
    )

    async def build_settings_view(page):
        return await build_settings_page(
            self, guild, channels, excluded_channel_ids, channels_per_page, total_pages, page
        )

    settings_view = await build_settings_view(0)

    await interaction.response.send_message(
        view=settings_view,
        ephemeral=True,
    )
