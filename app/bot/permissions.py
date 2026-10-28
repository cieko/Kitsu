from typing import Any

import discord
from discord import app_commands


async def require_guild_permission(
    interaction: discord.Interaction,
    permission: str,
    label: str,
) -> bool:
    """Check the acting member, including for public component callbacks."""
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message(
            "Use this control inside a server.", ephemeral=True
        )
        return False

    permissions = interaction.user.guild_permissions
    if not (permissions.administrator or getattr(permissions, permission)):
        await interaction.response.send_message(
            f"You need **{label}** permission to use this control.",
            ephemeral=True,
        )
        return False

    return True


class GuildPermissionGroup(app_commands.Group):
    """Apply both Discord defaults and runtime checks to a command group."""

    def __init__(self, *, permission: str, permission_label: str, **kwargs: Any):
        self.permission = permission
        self.permission_label = permission_label
        super().__init__(
            guild_only=True,
            default_permissions=discord.Permissions(**{permission: True}),
            **kwargs,
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await require_guild_permission(
            interaction, self.permission, self.permission_label
        )
