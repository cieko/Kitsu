from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .view import CustomChannelView
import discord


class GIFDomainsModal(discord.ui.Modal):
    def __init__(
        self,
        view: CustomChannelView,
    ):
        super().__init__(
            title="GIF Domains",
        )

        self.parent_view = view

        self.domains_input = discord.ui.TextInput(
            label="Allowed GIF domains",
            placeholder="tenor.com, giphy.com",
            # default=", ".join(
            #     view.gif_domains
            # ),
            required=False,
            max_length=1000,
        )

        self.add_item(self.domains_input)

    async def on_submit(
        self,
        interaction: discord.Interaction,
    ):

        raw_domains = self.domains_input.value.strip()

        # ----------------------------------------------------
        # Empty input
        # ----------------------------------------------------

        if not raw_domains:
            self.parent_view.gif_domains = []

        # ----------------------------------------------------
        # Wildcard
        # ----------------------------------------------------

        elif "*" in raw_domains:
            self.parent_view.gif_domains = ["*"]

        # ----------------------------------------------------
        # Normal domains
        # ----------------------------------------------------

        else:
            domains = []

            for domain in raw_domains.split(","):
                domain = domain.strip().lower()

                if not domain:
                    continue

                domain = domain.removeprefix("https://")

                domain = domain.removeprefix("http://")

                domain = domain.rstrip("/")

                if domain not in domains:
                    domains.append(domain)

            self.parent_view.gif_domains = domains

        # ----------------------------------------------------
        # Rebuild Page 2 so the new list is displayed
        # immediately.
        # ----------------------------------------------------

        self.parent_view._build_page_two()

        await interaction.response.edit_message(view=self.parent_view)
