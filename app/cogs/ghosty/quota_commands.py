from __future__ import annotations

import discord
from discord import app_commands

from app.services.ghosty_tempchannel_service import TempChannelError

from .groups import tempchannels


class QuotaCommands:
    @tempchannels.command(
        name="role-set",
        description="Set the personal and total quota for a Discord role.",
    )
    @app_commands.describe(
        role="Role receiving this quota.",
        personal="Maximum active channels per member with this role.",
        total="Maximum active channels assigned to this role's quota.",
    )
    async def tempchannel_roles_set(
        self,
        interaction: discord.Interaction,
        role: discord.Role,
        personal: app_commands.Range[int, 1, 100],
        total: app_commands.Range[int, 1, 1000],
    ) -> None:
        if not await self._require_tempchannel_staff(interaction):
            return

        assert interaction.guild is not None

        try:
            await self.tempchannel_service.set_role_limits(
                interaction.guild.id,
                role.id,
                personal,
                total,
                role.name,
            )
        except TempChannelError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        await interaction.response.send_message(
            f"Updated quota for {role.mention}:\n"
            f"• Personal active-channel limit: **{personal}**\n"
            f"• Total channels assigned to this role's quota: **{total}**",
            ephemeral=True,
        )

    @tempchannels.command(
        name="role-remove",
        description="Remove a role's custom quota and use the default quota.",
    )
    async def tempchannel_roles_remove(
        self,
        interaction: discord.Interaction,
        role: discord.Role,
    ) -> None:
        if not await self._require_tempchannel_staff(interaction):
            return

        assert interaction.guild is not None

        await self.tempchannel_service.remove_role_limits(interaction.guild.id, role.id)

        await interaction.response.send_message(
            f"Removed the custom quota for {role.mention}. "
            "Future channels will use the highest applicable configured role "
            "or the default quota.",
            ephemeral=True,
        )

    @tempchannels.command(
        name="role-list",
        description="List configured role quotas and default quotas.",
    )
    async def tempchannel_roles_list(
        self,
        interaction: discord.Interaction,
    ) -> None:
        if not await self._require_tempchannel_staff(interaction):
            return

        assert interaction.guild is not None

        settings = await self.tempchannel_service.get_settings(interaction.guild.id)

        lines = [
            "**Default quota**",
            f"Personal: **{settings.get('default_personal_limit', 1)}**",
            f"Total: **{settings.get('default_total_limit', 10)}**",
            "",
            "**Role quotas**",
        ]

        role_limits = settings.get("role_limits", [])
        for item in role_limits:
            role_id = int(item["role_id"])
            role = interaction.guild.get_role(role_id)
            role_name = role.mention if role else item.get("role_name", str(role_id))

            lines.append(
                f"{role_name} — personal: **{item['personal']}**, total: **{item['total']}**"
            )

        if not role_limits:
            lines.append("No custom role quotas configured.")

        await interaction.response.send_message(
            "\n".join(lines),
            ephemeral=True,
        )

    @tempchannels.command(
        name="staff-add",
        description="Give a role protected staff access to temporary channels.",
    )
    async def tempchannel_staff_add(
        self,
        interaction: discord.Interaction,
        role: discord.Role,
    ) -> None:
        if not await self._require_tempchannel_staff(interaction):
            return

        assert interaction.guild is not None

        settings = await self.tempchannel_service.get_settings(interaction.guild.id)
        role_ids = {int(role_id) for role_id in settings.get("staff_role_ids", [])}

        if role.id in role_ids:
            await interaction.response.send_message(
                f"{role.mention} is already a protected staff role.",
                ephemeral=True,
            )
            return

        role_ids.add(role.id)
        await self.tempchannel_service.set_staff_roles(interaction.guild.id, list(role_ids))

        await interaction.response.send_message(
            f"Added {role.mention} to the configured staff roles. "
            "This applies to channels when their permissions are next updated; "
            "existing channels may need their permissions refreshed.",
            ephemeral=True,
        )

    @tempchannels.command(
        name="staff-remove",
        description="Remove a role from the protected staff-role list.",
    )
    async def tempchannel_staff_remove(
        self,
        interaction: discord.Interaction,
        role: discord.Role,
    ) -> None:
        if not await self._require_tempchannel_staff(interaction):
            return

        assert interaction.guild is not None

        settings = await self.tempchannel_service.get_settings(interaction.guild.id)
        role_ids = {int(role_id) for role_id in settings.get("staff_role_ids", [])}

        if role.id not in role_ids:
            await interaction.response.send_message(
                f"{role.mention} is not a configured staff role.",
                ephemeral=True,
            )
            return

        role_ids.remove(role.id)
        await self.tempchannel_service.set_staff_roles(interaction.guild.id, list(role_ids))

        await interaction.response.send_message(
            f"Removed {role.mention} from the configured staff roles. "
            "Existing channel permission overwrites may need to be refreshed.",
            ephemeral=True,
        )

    @tempchannels.command(
        name="staff-list",
        description="List protected temporary-channel staff roles.",
    )
    async def tempchannel_staff_list(
        self,
        interaction: discord.Interaction,
    ) -> None:
        if not await self._require_tempchannel_staff(interaction):
            return

        assert interaction.guild is not None

        settings = await self.tempchannel_service.get_settings(interaction.guild.id)
        role_ids = settings.get("staff_role_ids", [])

        role_lines = []
        for role_id in role_ids:
            role = interaction.guild.get_role(int(role_id))
            role_lines.append(role.mention if role else f"Deleted role (`{role_id}`)")

        await interaction.response.send_message(
            "**Protected temporary-channel staff roles**\n"
            + ("\n".join(role_lines) if role_lines else "None configured."),
            ephemeral=True,
        )
