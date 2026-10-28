from __future__ import annotations

from typing import TYPE_CHECKING

from .member_selects import AddMemberSelect, RemoveMemberSelect

if TYPE_CHECKING:
    from .cog import GhostyCog

import discord

from app.services.ghosty_tempchannel_service import TempChannelError

from .constants import TEMPCHANNEL_SETTINGS_COLOR


class GhostyTempChannelSettingsView(discord.ui.LayoutView):
    """Interactive settings panel for a Ghosty temporary-channel owner."""

    visibility_row = discord.ui.ActionRow()

    duration_row = discord.ui.ActionRow()

    delete_row = discord.ui.ActionRow()

    def __init__(
        self,
        cog: "GhostyCog",
        owner_id: int,
        channel_id: int,
        server_name: str,
        thumbnail_url: str | None = None,
    ) -> None:
        super().__init__(timeout=300)
        self.cog = cog
        self.owner_id = owner_id
        self.channel_id = channel_id

        # Place the dropdown rows inside the card and deletion below it.
        self.clear_items()
        header = discord.ui.TextDisplay(
            f"### {discord.utils.escape_markdown(server_name)}\n"
            "**Ghosty Temporary Channel Settings**\n"
            "Manage your channel's visibility, members, and expiry below."
        )
        if thumbnail_url:
            heading = discord.ui.Section(
                header,
                accessory=discord.ui.Thumbnail(
                    thumbnail_url,
                    description="Temporary-channel settings",
                ),
            )
        else:
            heading = header

        container = discord.ui.Container(
            heading,
            discord.ui.Separator(),
            self.visibility_row,
            self.duration_row,
            discord.ui.ActionRow(self.AddMemberSelect(self)),
            discord.ui.ActionRow(self.RemoveMemberSelect(self)),
            discord.ui.TextDisplay(
                "-# Only this channel's owner can use these controls.\n"
                "-# Slash commands are disabled here; use this settings panel."
            ),
            accent_colour=TEMPCHANNEL_SETTINGS_COLOR,
        )
        self.add_item(container)
        self.add_item(self.delete_row)

    @property
    def channel(self) -> discord.TextChannel | None:
        """Return the original channel if it is still available."""
        channel = self.cog.bot.get_channel(self.channel_id)
        if isinstance(channel, discord.TextChannel):
            return channel
        return None

    async def interaction_check(
        self,
        interaction: discord.Interaction,
    ) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "Only the owner of this temporary channel can use these controls.",
                ephemeral=True,
            )
            return False

        if (
            interaction.guild is None
            or interaction.channel_id != self.channel_id
            or not isinstance(interaction.user, discord.Member)
            or not isinstance(interaction.channel, discord.TextChannel)
        ):
            await interaction.response.send_message(
                "Use this panel in its original temporary channel.",
                ephemeral=True,
            )
            return False

        try:
            await self.cog.tempchannel_service._get_owned_channel(
                interaction.user,
                interaction.channel,
            )
        except TempChannelError as exc:
            await interaction.response.send_message(
                str(exc),
                ephemeral=True,
            )
            return False

        return True

    @visibility_row.select(
        placeholder="Change visibility...",
        options=[
            discord.SelectOption(
                label="Public",
                value="public",
                description="Allow everyone to view the channel.",
            ),
            discord.SelectOption(
                label="Private",
                value="private",
                description="Restrict access to the owner and allowed members.",
            ),
        ],
    )
    async def visibility_select(
        self,
        interaction: discord.Interaction,
        select: discord.ui.Select,
    ) -> None:
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

        visibility = select.values[0]

        try:
            await self.cog.tempchannel_service.set_visibility(
                interaction.user,
                interaction.channel,
                visibility,
            )
        except TempChannelError as exc:
            await interaction.response.send_message(
                str(exc),
                ephemeral=True,
            )
            return
        except discord.HTTPException:
            await interaction.response.send_message(
                "Discord could not update the channel permissions.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"Channel visibility changed to **{visibility}**.",
            ephemeral=True,
        )

    @duration_row.select(
        placeholder="Extend channel duration...",
        options=[
            discord.SelectOption(label="1 hour", value="1h"),
            discord.SelectOption(label="2 hours", value="2h"),
            discord.SelectOption(label="6 hours", value="6h"),
            discord.SelectOption(label="12 hours", value="12h"),
            discord.SelectOption(label="1 day", value="1d"),
            discord.SelectOption(label="3 days", value="3d"),
            discord.SelectOption(label="7 days", value="7d"),
        ],
    )
    async def duration_select(
        self,
        interaction: discord.Interaction,
        select: discord.ui.Select,
    ) -> None:
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

        try:
            expires_at = await self.cog.tempchannel_service.extend_channel(
                interaction.user,
                interaction.channel,
                select.values[0],
            )
        except TempChannelError as exc:
            await interaction.response.send_message(
                str(exc),
                ephemeral=True,
            )
            return
        except discord.HTTPException:
            await interaction.response.send_message(
                "Discord could not extend the channel duration.",
                ephemeral=True,
            )
            return

        timestamp = int(expires_at.timestamp())
        await interaction.response.send_message(
            f"Channel expiry updated to <t:{timestamp}:F> (<t:{timestamp}:R>).",
            ephemeral=True,
        )

    @delete_row.button(
        label="Delete channel",
        style=discord.ButtonStyle.danger,
    )
    async def delete_channel_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        confirmation = discord.ui.View(timeout=60)

        async def confirm_delete(
            confirm_interaction: discord.Interaction,
        ) -> None:
            if confirm_interaction.user.id != self.owner_id:
                await confirm_interaction.response.send_message(
                    "Only the channel owner can confirm deletion.",
                    ephemeral=True,
                )
                return

            if (
                confirm_interaction.guild is None
                or confirm_interaction.channel_id != self.channel_id
                or not isinstance(confirm_interaction.user, discord.Member)
                or not isinstance(
                    confirm_interaction.channel,
                    discord.TextChannel,
                )
            ):
                await confirm_interaction.response.send_message(
                    "Return to the original temporary channel.",
                    ephemeral=True,
                )
                return

            # Acknowledge the interaction before deleting the channel.
            await confirm_interaction.response.defer(
                ephemeral=True,
                thinking=True,
            )

            try:
                await self.cog.tempchannel_service.delete_owned_channel(
                    confirm_interaction.user,
                    confirm_interaction.channel,
                )
            except TempChannelError as exc:
                await confirm_interaction.followup.send(
                    str(exc),
                    ephemeral=True,
                )
                return
            except discord.HTTPException:
                await confirm_interaction.followup.send(
                    "Discord could not delete the channel.",
                    ephemeral=True,
                )
                return

            # await confirm_interaction.response.send_message(
            #     "Your temporary channel has been deleted.",
            #     ephemeral=True,
            # )

        async def cancel_delete(
            cancel_interaction: discord.Interaction,
        ) -> None:
            if cancel_interaction.user.id != self.owner_id:
                await cancel_interaction.response.send_message(
                    "Only the channel owner can cancel this confirmation.",
                    ephemeral=True,
                )
                return

            await cancel_interaction.response.edit_message(
                content="Channel deletion cancelled.",
                view=None,
            )

        confirm_button = discord.ui.Button(
            label="Confirm deletion",
            style=discord.ButtonStyle.danger,
        )
        cancel_button = discord.ui.Button(
            label="Cancel",
            style=discord.ButtonStyle.secondary,
        )

        confirm_button.callback = confirm_delete
        cancel_button.callback = cancel_delete

        confirmation.add_item(confirm_button)
        confirmation.add_item(cancel_button)

        await interaction.response.send_message(
            "Are you sure you want to permanently delete this temporary channel?",
            view=confirmation,
            ephemeral=True,
        )

    AddMemberSelect = AddMemberSelect
    RemoveMemberSelect = RemoveMemberSelect
