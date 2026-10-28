from __future__ import annotations

import discord
from discord import app_commands

from app.database.repositories import (
    ghosty_tempchannel_repository as tempchannel_repository,
)

from .groups import tempchannels


class StaffCommands:
    @tempchannels.command(
        name="settings",
        description="View or enable/disable temporary-channel creation.",
    )
    @app_commands.describe(enabled="Whether members can create new temporary channels.")
    async def tempchannels_settings(
        self,
        interaction: discord.Interaction,
        enabled: bool | None = None,
    ) -> None:
        if not await self._require_tempchannel_staff(interaction):
            return

        assert interaction.guild is not None

        if enabled is not None:
            await self.tempchannel_service.set_enabled(interaction.guild.id, enabled)

        settings = await self.tempchannel_service.get_settings(interaction.guild.id)

        staff_roles = settings.get("staff_role_ids", [])
        role_limits = settings.get("role_limits", [])

        embed = discord.Embed(
            title="Ghosty Temporary-Channel Settings",
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="Creation",
            value="Enabled" if settings.get("enabled", True) else "Disabled",
            inline=True,
        )
        embed.add_field(
            name="Category",
            value=f"<#{settings.get('category_id', 0)}>",
            inline=True,
        )
        embed.add_field(
            name="Default quotas",
            value=(
                f"Personal: **{settings.get('default_personal_limit', 1)}**\n"
                f"Total: **{settings.get('default_total_limit', 10)}**"
            ),
            inline=True,
        )
        embed.add_field(
            name="Member cap per channel",
            value=str(settings.get("max_ordinary_members", 20)),
            inline=True,
        )
        embed.add_field(
            name="Protected staff roles",
            value=", ".join(f"<@&{role_id}>" for role_id in staff_roles)
            if staff_roles
            else "None configured",
            inline=False,
        )
        embed.add_field(
            name="Role quota configurations",
            value=str(len(role_limits)),
            inline=True,
        )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @tempchannels.command(
        name="list",
        description="List active Ghosty temporary channels.",
    )
    async def tempchannels_list(
        self,
        interaction: discord.Interaction,
    ) -> None:
        if not await self._require_tempchannel_staff(interaction):
            return

        assert interaction.guild is not None

        records = await tempchannel_repository.get_all_active_channels(interaction.guild.id)

        if not records:
            await interaction.response.send_message(
                "There are no active temporary channels.",
                ephemeral=True,
            )
            return

        lines: list[str] = []
        for record in records:
            channel_id = int(record["channel_id"])
            creator_id = int(record["creator_id"])
            channel = interaction.guild.get_channel(channel_id)
            channel_text = channel.mention if channel else f"`{channel_id}` (not found)"

            expires_at = record.get("expires_at")
            if expires_at is not None:
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=discord.utils.utcnow().tzinfo)
                expiry_text = f"<t:{int(expires_at.timestamp())}:R>"
            else:
                expiry_text = "Unknown expiry"

            lines.append(f"{channel_text} — owner <@{creator_id}> — {expiry_text}")

        # Discord message content has a 2,000-character limit.
        pages = []
        current_page = []
        current_length = 0

        for line in lines:
            if current_page and current_length + len(line) + 1 > 1800:
                pages.append(current_page)
                current_page = []
                current_length = 0

            current_page.append(line)
            current_length += len(line) + 1

        if current_page:
            pages.append(current_page)

        await interaction.response.send_message(
            f"**Active temporary channels: {len(records)}**\n" + "\n".join(pages[0]),
            ephemeral=True,
        )

        for index, page in enumerate(pages[1:], start=2):
            await interaction.followup.send(
                f"**Temporary channels — page {index}**\n" + "\n".join(page),
                ephemeral=True,
            )

    @tempchannels.command(
        name="delete",
        description="Manually delete an active temporary channel by ID.",
    )
    @app_commands.describe(channel_id="The numeric Discord channel ID to delete.")
    async def tempchannels_delete(
        self,
        interaction: discord.Interaction,
        channel_id: str,
    ) -> None:
        if not await self._require_tempchannel_staff(interaction):
            return

        assert interaction.guild is not None

        try:
            parsed_channel_id = int(channel_id.strip())
        except (TypeError, ValueError):
            await interaction.response.send_message(
                "Enter a valid numeric channel ID.",
                ephemeral=True,
            )
            return

        record = await tempchannel_repository.get_active_channel(
            interaction.guild.id,
            parsed_channel_id,
        )
        if record is None:
            await interaction.response.send_message(
                "No active temporary-channel record was found for that ID.",
                ephemeral=True,
            )
            return

        channel = interaction.guild.get_channel(parsed_channel_id)
        channel_label = f"#{channel.name}" if channel else str(parsed_channel_id)

        await self.tempchannel_service.delete_channel(
            interaction.guild,
            parsed_channel_id,
        )

        await interaction.response.send_message(
            f"Processed manual deletion for temporary channel **{channel_label}**.",
            ephemeral=True,
        )
