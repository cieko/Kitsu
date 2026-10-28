from __future__ import annotations

import discord

from app.services.ghosty_tempchannel_service import TempChannelError


class GhostyGuards:
    async def _require_guild(
        self,
        interaction: discord.Interaction,
    ) -> bool:
        if interaction.guild is None:
            await interaction.response.send_message(
                "Use this command inside a server.",
                ephemeral=True,
            )
            return False

        return True

    async def _require_admin(
        self,
        interaction: discord.Interaction,
    ) -> bool:
        if interaction.guild is None:
            await interaction.response.send_message(
                "Use this command inside a server.",
                ephemeral=True,
            )
            return False

        permissions = interaction.user.guild_permissions

        if not (permissions.administrator or permissions.manage_guild):
            await interaction.response.send_message(
                "You need Manage Server permission to use this command.",
                ephemeral=True,
            )
            return False

        return True

    async def _get_message(
        self,
        channel: discord.abc.Messageable,
        message_id: int,
    ) -> discord.Message | None:
        try:
            return await channel.fetch_message(message_id)
        except (
            discord.NotFound,
            discord.Forbidden,
            discord.HTTPException,
        ):
            return None

    async def _require_tempchannel_staff(
        self,
        interaction: discord.Interaction,
    ) -> bool:
        """Require the staff channel and an authorized staff member."""

        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "Use this command inside the server.",
                ephemeral=True,
            )
            return False

        if interaction.channel_id != self.TEMPCHANNEL_STAFF_CHANNEL_ID:
            await interaction.response.send_message(
                "Use temporary-channel staff commands in the designated staff channel.",
                ephemeral=True,
            )
            return False

        # Server managers can bootstrap or repair the configured staff roles.
        if interaction.user.guild_permissions.manage_guild:
            return True

        settings = await self.tempchannel_service.get_settings(interaction.guild.id)
        staff_role_ids = {int(role_id) for role_id in settings.get("staff_role_ids", [])}

        if any(role.id in staff_role_ids for role in interaction.user.roles):
            return True

        await interaction.response.send_message(
            "You need Manage Server or a configured temporary-channel staff role.",
            ephemeral=True,
        )
        return False

    async def _get_owned_tempchannel_context(
        self,
        interaction: discord.Interaction,
    ) -> tuple[discord.Member, discord.TextChannel] | None:
        """Resolve the current channel and require an active owned channel."""

        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "Use this command inside the server.",
                ephemeral=True,
            )
            return None

        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message(
                "Use this command inside a temporary text channel.",
                ephemeral=True,
            )
            return None

        try:
            # This checks that the channel is active and belongs to the user.
            await self.tempchannel_service._get_owned_channel(
                interaction.user,
                interaction.channel,
            )
        except TempChannelError as exc:
            await interaction.response.send_message(
                str(exc),
                ephemeral=True,
            )
            return None

        return interaction.user, interaction.channel
