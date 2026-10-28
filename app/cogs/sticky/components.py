from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .views import StickyListView

import discord

from app.database.repositories import sticky_repository as repository

from .helpers import build_sticky_options


class StickyActionButton(discord.ui.Button):
    def __init__(
        self,
        *,
        label: str,
        style: discord.ButtonStyle,
        callback,
        emoji: str | None = None,
        disabled: bool = False,
    ):
        super().__init__(
            label=label,
            style=style,
            emoji=emoji,
            disabled=disabled,
        )
        self.action_callback = callback

    async def callback(self, interaction: discord.Interaction):
        await self.action_callback(interaction)


class StickyEditModal(discord.ui.Modal):
    def __init__(self, manager: "StickyListView", script: str):
        super().__init__(title="Edit Sticky Message")

        self.manager = manager
        self.channel_id = manager.selected_channel_id

        self.message_input = discord.ui.TextInput(
            label="Message or embed script",
            style=discord.TextStyle.paragraph,
            default=script[:4000],
            max_length=4000,
            required=True,
        )
        self.add_item(self.message_input)

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        channel_id = self.channel_id

        if not await self.manager.interaction_check(interaction):
            return

        if guild is None or channel_id is None:
            await interaction.response.send_message(
                "Select a sticky message first.",
                ephemeral=True,
            )
            return

        await interaction.response.defer()
        try:
            script = self.message_input.value
            content, embed = self.manager.sticky_service.render(
                script,
                guild,
            )

            if not content and embed is None:
                raise ValueError("The sticky message cannot be empty.")

            sticky = await repository.get_sticky(guild.id, channel_id)
            if sticky is None:
                await interaction.followup.send(
                    "That sticky no longer exists.",
                    ephemeral=True,
                )
                return

            channel = guild.get_channel(channel_id)
            if not isinstance(channel, discord.TextChannel):
                raise ValueError("The sticky channel no longer exists.")
            await self.manager.cog.configure_sticky(
                channel, script, interaction.user.id, require_existing=True
            )

            self.manager.stickies = await repository.get_stickies(guild.id)

            # Update the existing Components V2 message.
            self.manager.selected_channel_id = channel_id
            await self.manager.show_detail(interaction)

        except ValueError as exc:
            await interaction.followup.send(
                f"Invalid sticky script: {exc}",
                ephemeral=True,
            )
        except Exception as exc:
            await self.manager.cog.report_error(interaction, exc)


class StickyChannelSelect(discord.ui.Select):
    def __init__(self, manager: "StickyListView"):
        self.manager = manager

        options = build_sticky_options(
            manager.guild,
            manager.visible_stickies(),
        )

        super().__init__(
            placeholder="Select a sticky message...",
            min_values=1,
            max_values=1,
            options=options,
            disabled=not options,
        )

    async def callback(self, interaction: discord.Interaction):
        self.manager.selected_channel_id = int(self.values[0])
        await self.manager.show_detail(interaction)
