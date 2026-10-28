from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .cog import Sticky

import discord

from app.database.repositories import sticky_repository as repository
from app.services.sticky_service import StickyService

from .components import StickyActionButton, StickyChannelSelect, StickyEditModal
from .helpers import get_channel_name, get_sticky_preview


class StickyListView(discord.ui.LayoutView):
    def __init__(
        self,
        guild: discord.Guild,
        stickies: list[dict],
        *,
        cog: "Sticky",
    ):
        super().__init__(timeout=300)

        self.guild = guild
        self.cog = cog
        self.stickies = stickies
        self.page = 0
        self.selected_channel_id: int | None = None
        self.sticky_service = StickyService()

        self.show_home()

    async def interaction_check(
        self,
        interaction: discord.Interaction,
    ) -> bool:
        if interaction.guild is None or interaction.guild.id != self.guild.id:
            await interaction.response.send_message(
                "This control can only be used inside a server.",
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
                "You need Administrator permission to manage stickies.",
                ephemeral=True,
            )
            return False

        return True

    async def on_error(
        self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item
    ):
        await self.cog.report_error(interaction, error)

    def visible_stickies(self) -> list[dict]:
        return self.stickies[self.page * 10 : (self.page + 1) * 10]

    def selected_sticky(self) -> dict | None:
        return next(
            (
                sticky
                for sticky in self.stickies
                if sticky["channel_id"] == self.selected_channel_id
            ),
            None,
        )

    # --------------------------------------------------
    # HOME / LIST SCREEN
    # --------------------------------------------------

    def show_home(self):
        self.clear_items()
        pages = max(1, (len(self.stickies) + 9) // 10)
        self.page = min(self.page, pages - 1)

        container = discord.ui.Container(
            discord.ui.TextDisplay(
                f"## Sticky messages\n-# Entries · {len(self.stickies)} configured"
            ),
            discord.ui.Separator(),
        )

        if not self.stickies:
            container.add_item(discord.ui.TextDisplay("No sticky messages configured."))
        else:
            for sticky in self.visible_stickies():
                channel_name = get_channel_name(
                    self.guild,
                    sticky["channel_id"],
                )

                preview = get_sticky_preview(sticky)

                container.add_item(discord.ui.TextDisplay(f"> **{channel_name}**\n> {preview}"))
                container.add_item(discord.ui.Separator())

            container.add_item(discord.ui.ActionRow(StickyChannelSelect(self)))

            if pages > 1:
                container.add_item(
                    discord.ui.ActionRow(
                        StickyActionButton(
                            label="Previous",
                            style=discord.ButtonStyle.secondary,
                            callback=lambda interaction: self.turn_page(interaction, -1),
                            disabled=self.page == 0,
                        ),
                        StickyActionButton(
                            label="Next",
                            style=discord.ButtonStyle.secondary,
                            callback=lambda interaction: self.turn_page(interaction, 1),
                            disabled=self.page == pages - 1,
                        ),
                    )
                )
                container.add_item(discord.ui.TextDisplay(f"-# Page {self.page + 1}/{pages}"))

        self.add_item(container)

    async def turn_page(self, interaction: discord.Interaction, delta: int):
        self.page = max(0, self.page + delta)
        self.show_home()
        await self.update_message(interaction)

    # --------------------------------------------------
    # DETAIL SCREEN
    # --------------------------------------------------

    async def show_detail(
        self,
        interaction: discord.Interaction,
    ):
        guild = interaction.guild

        if guild is None:
            return

        if not interaction.response.is_done():
            await interaction.response.defer()
        self.stickies = await repository.get_stickies(guild.id)
        sticky = self.selected_sticky()

        if sticky is None:
            self.selected_channel_id = None
            self.show_home()

            await self.update_message(interaction)
            return

        self.clear_items()

        channel_id = sticky["channel_id"]
        channel_name = get_channel_name(guild, channel_id)

        script = sticky.get("message_script", "").strip()
        if not script:
            script = "*Empty message*"

        # Avoid exceeding Discord's TextDisplay component limit.
        if len(script) > 3500:
            script = script[:3497] + "..."

        ignore_bots = sticky.get("ignore_bots", True)

        edit_button = StickyActionButton(
            label="Edit",
            style=discord.ButtonStyle.secondary,
            callback=self.edit_selected,
        )

        ignore_button = StickyActionButton(
            label="On" if ignore_bots else "Off",
            style=(discord.ButtonStyle.success if ignore_bots else discord.ButtonStyle.secondary),
            callback=self.toggle_ignore_bots,
        )

        container = discord.ui.Container(
            discord.ui.TextDisplay(f"### {channel_name}\n-# Sticky messages"),
            discord.ui.Separator(),
            discord.ui.Section(
                discord.ui.TextDisplay(f"**Message**\n{script}"),
                accessory=edit_button,
            ),
            discord.ui.Separator(),
            discord.ui.Section(
                discord.ui.TextDisplay("**Ignore bots**\nDo not repost after a bot's message"),
                accessory=ignore_button,
            ),
            discord.ui.Separator(),
            accent_colour=discord.Colour.from_rgb(180, 214, 213),
        )

        back_button = StickyActionButton(
            label="Back",
            style=discord.ButtonStyle.secondary,
            callback=self.back_to_list,
        )

        remove_button = StickyActionButton(
            label="Remove",
            style=discord.ButtonStyle.danger,
            callback=self.remove_selected,
        )

        container.add_item(
            discord.ui.ActionRow(
                back_button,
                remove_button,
            )
        )

        self.add_item(container)

        await self.update_message(interaction)

    async def update_message(self, interaction: discord.Interaction):
        if interaction.response.is_done():
            await interaction.edit_original_response(view=self)
        else:
            await interaction.response.edit_message(view=self)

    # --------------------------------------------------
    # EDIT
    # --------------------------------------------------

    async def edit_selected(self, interaction: discord.Interaction):
        sticky = self.selected_sticky()

        if sticky is None:
            await interaction.response.send_message(
                "Select a sticky message first.",
                ephemeral=True,
            )
            return

        await interaction.response.send_modal(
            StickyEditModal(
                self,
                sticky.get("message_script", ""),
            )
        )

    # --------------------------------------------------
    # TOGGLE IGNORE BOTS
    # --------------------------------------------------

    async def toggle_ignore_bots(
        self,
        interaction: discord.Interaction,
    ):
        guild = interaction.guild

        if guild is None or self.selected_channel_id is None:
            await interaction.response.send_message(
                "Select a sticky message first.",
                ephemeral=True,
            )
            return

        await interaction.response.defer()
        try:
            async with self.cog._lock(self.selected_channel_id):
                sticky = await repository.get_sticky(guild.id, self.selected_channel_id)
                if sticky is None:
                    raise ValueError("That sticky no longer exists.")
                await repository.set_sticky_ignore_bots(
                    guild.id, self.selected_channel_id, not sticky.get("ignore_bots", True)
                )
            await self.show_detail(interaction)
        except Exception as exc:
            await self.cog.report_error(interaction, exc)

    # --------------------------------------------------
    # BACK / HOME
    # --------------------------------------------------

    async def back_to_list(self, interaction: discord.Interaction):
        await interaction.response.defer()
        self.selected_channel_id = None
        self.stickies = await repository.get_stickies(self.guild.id)

        self.show_home()

        await self.update_message(interaction)

    # --------------------------------------------------
    # REMOVE
    # --------------------------------------------------

    async def remove_selected(self, interaction: discord.Interaction):
        guild = interaction.guild
        channel_id = self.selected_channel_id

        if guild is None or channel_id is None:
            await interaction.response.send_message(
                "Select a sticky message first.",
                ephemeral=True,
            )
            return

        channel = guild.get_channel(channel_id)
        await interaction.response.defer()
        try:
            await self.cog.remove_sticky(guild, channel_id, channel)
        except Exception as exc:
            await self.cog.report_error(interaction, exc)
            return

        self.selected_channel_id = None
        self.stickies = await repository.get_stickies(guild.id)

        self.show_home()

        await self.update_message(interaction)
