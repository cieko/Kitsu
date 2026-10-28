from __future__ import annotations

import discord

from .constants import CONTENT_TYPES, DEFAULT_GIF_DOMAINS
from .modals import GIFDomainsModal


class GifDomainsPage:
    def _build_page_two(self):

        self.clear_items()

        self.current_page = 2

        container = discord.ui.Container(
            accent_color=discord.Color.from_rgb(115, 5, 43),
        )

        # ----------------------------------------------------
        # Header
        # ----------------------------------------------------

        container.add_item(
            discord.ui.TextDisplay(
                f"# [⚙️](https://discord.com/assets/7afdc0163bb3fba3.svg) "
                f"Custom Channel Setup\n"
                f"**Channel:** {self.channel.mention}\n\n"
                "Choose which domains are allowed for GIF links."
            )
        )

        # ----------------------------------------------------
        # Current domains
        # ----------------------------------------------------

        container.add_item(discord.ui.TextDisplay(self._gif_domains_text()))

        # ----------------------------------------------------
        # Edit domains
        # ----------------------------------------------------

        edit_row = discord.ui.ActionRow()

        edit_button = discord.ui.Button(
            label="Edit Domains",
            style=discord.ButtonStyle.secondary,
            emoji="✏️",
            custom_id="custom_edit_domains",
        )

        edit_button.callback = self.edit_gif_domains

        edit_row.add_item(edit_button)

        container.add_item(edit_row)

        self.add_item(container)

        # ----------------------------------------------------
        # Navigation
        # ----------------------------------------------------

        navigation = discord.ui.ActionRow()

        back_button = discord.ui.Button(
            label="◀",
            style=discord.ButtonStyle.secondary,
            custom_id="custom_page2_back",
        )

        skip_button = discord.ui.Button(
            label="Skip",
            style=discord.ButtonStyle.secondary,
            custom_id="custom_page2_skip",
        )

        continue_button = discord.ui.Button(
            label="Save & Continue",
            style=discord.ButtonStyle.primary,
            custom_id="custom_page2_continue",
        )

        cancel_button = discord.ui.Button(
            label="Cancel",
            style=discord.ButtonStyle.danger,
            custom_id="custom_page2_cancel",
        )

        back_button.callback = self.page_two_back
        skip_button.callback = self.skip_page_two
        continue_button.callback = self.page_two_continue
        cancel_button.callback = self.cancel

        navigation.add_item(back_button)
        navigation.add_item(skip_button)
        navigation.add_item(continue_button)
        navigation.add_item(cancel_button)

        self.add_item(navigation)

    def _gif_domains_text(self) -> str:

        if "*" in self.gif_domains:
            return "**Allowed Domains**\n🌐 All GIF domains are allowed."

        if not self.gif_domains:
            return "**Allowed Domains**\n`None`"

        domains = "\n".join(f"• `{domain}`" for domain in self.gif_domains)

        return f"**Allowed Domains**\n{domains}"

    async def edit_gif_domains(
        self,
        interaction: discord.Interaction,
    ):

        modal = GIFDomainsModal(
            self,
        )

        await interaction.response.send_modal(
            modal,
        )

    async def page_two_back(
        self,
        interaction: discord.Interaction,
    ):

        self._build_page_one()

        for key in CONTENT_TYPES:
            self._update_button_state(key)

        await interaction.response.edit_message(
            view=self,
        )

    async def skip_page_two(
        self,
        interaction: discord.Interaction,
    ):

        self.gif_domains = DEFAULT_GIF_DOMAINS.copy()

        self._build_page_three()

        await interaction.response.edit_message(
            view=self,
        )

    async def page_two_continue(
        self,
        interaction: discord.Interaction,
    ):

        self._build_page_three()

        await interaction.response.edit_message(
            view=self,
        )
