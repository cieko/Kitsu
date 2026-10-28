from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .tempchannel_settings import GhostyTempChannelSettingsView

import discord

from app.services.ghosty_tempchannel_service import TempChannelError


class AddMemberSelect(discord.ui.UserSelect):
    """Select a server member to add to the temporary channel."""

    def __init__(
        self,
        settings_view: "GhostyTempChannelSettingsView",
    ) -> None:
        self.settings_view = settings_view
        super().__init__(
            placeholder="Select a member to add...",
            min_values=1,
            max_values=1,
        )

    async def callback(
        self,
        interaction: discord.Interaction,
    ) -> None:
        view = self.settings_view

        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "This action must be used inside the server.",
                ephemeral=True,
            )
            return

        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message(
                "This is not a text channel.",
                ephemeral=True,
            )
            return

        target = self.values[0]
        if not isinstance(target, discord.Member):
            await interaction.response.send_message(
                "Please select a server member.",
                ephemeral=True,
            )
            return

        try:
            await view.cog.tempchannel_service.add_member(
                interaction.user,
                interaction.channel,
                target,
            )
        except TempChannelError as exc:
            await interaction.response.send_message(
                str(exc),
                ephemeral=True,
            )
            return
        except discord.HTTPException:
            await interaction.response.send_message(
                "Discord could not update that member's permissions.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"{target.mention} now has access to this channel.",
            ephemeral=True,
        )


class RemoveMemberSelect(discord.ui.UserSelect):
    """Select an added member to remove from the temporary channel."""

    def __init__(
        self,
        settings_view: "GhostyTempChannelSettingsView",
    ) -> None:
        self.settings_view = settings_view
        super().__init__(
            placeholder="Select an added member to remove...",
            min_values=1,
            max_values=1,
        )

    async def callback(
        self,
        interaction: discord.Interaction,
    ) -> None:
        view = self.settings_view

        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "This action must be used inside the server.",
                ephemeral=True,
            )
            return

        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message(
                "This is not a text channel.",
                ephemeral=True,
            )
            return

        target = self.values[0]
        if not isinstance(target, discord.Member):
            await interaction.response.send_message(
                "Please select a server member.",
                ephemeral=True,
            )
            return

        try:
            await view.cog.tempchannel_service.remove_member(
                interaction.user,
                interaction.channel,
                target,
            )
        except TempChannelError as exc:
            await interaction.response.send_message(
                str(exc),
                ephemeral=True,
            )
            return
        except discord.HTTPException:
            await interaction.response.send_message(
                "Discord could not update that member's permissions.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"Removed {target.mention} from this channel.",
            ephemeral=True,
        )
