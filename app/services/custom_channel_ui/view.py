from __future__ import annotations

import discord

from app.bot.permissions import require_guild_permission
from app.database.repositories.channel_repository import set_channel_config
from app.services.emoji_services import get_application_emoji

from .constants import DEFAULT_GIF_DOMAINS, DEFAULT_RULES
from .content import ContentPage
from .gif_domains import GifDomainsPage
from .whitelist import WhitelistPage


class CustomChannelView(ContentPage, GifDomainsPage, WhitelistPage, discord.ui.LayoutView):
    def __init__(
        self,
        guild_id: int,
        channel: discord.TextChannel,
        bot: discord.Client,
        owner_id: int,
    ):
        super().__init__(timeout=300)

        self.guild_id = guild_id
        self.channel = channel
        self.bot = bot
        self.owner_id = owner_id

        # ----------------------------------------------------
        # Temporary configuration
        #
        # Nothing is saved to MongoDB until final save.
        # ----------------------------------------------------

        self.rules = DEFAULT_RULES.copy()

        self.gif_domains = DEFAULT_GIF_DOMAINS.copy()

        self.whitelist_users: set[int] = set()
        self.whitelist_roles: set[int] = set()

        # ----------------------------------------------------
        # Application emoji cache
        # ----------------------------------------------------

        self.emojis: dict[str, str] = {}

        # ----------------------------------------------------
        # Page state
        # ----------------------------------------------------

        self.current_page = 1

        # ----------------------------------------------------
        # Page 1 button references
        # ----------------------------------------------------

        self.label_buttons: dict[str, discord.ui.Button] = {}
        self.tick_buttons: dict[str, discord.ui.Button] = {}
        self.cross_buttons: dict[str, discord.ui.Button] = {}

        # ----------------------------------------------------
        # Build loading state first.
        #
        # The actual Page 1 UI is built after the application
        # emojis have finished loading.
        # ----------------------------------------------------

        self._build_loading_state()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.guild_id != self.guild_id or interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "This setup belongs to another member or server.", ephemeral=True
            )
            return False
        return await require_guild_permission(interaction, "manage_channels", "Manage Channels")

    def _build_loading_state(self):

        self.clear_items()

        self.current_page = 1

        container = discord.ui.Container(
            accent_color=discord.Color.from_rgb(115, 5, 43),
        )

        container.add_item(
            discord.ui.TextDisplay(
                f"# [⚙️](https://discord.com/assets/7afdc0163bb3fba3.svg) "
                f"Custom Channel Setup\n"
                f"**Channel:** {self.channel.mention}\n\n"
                "⏳ **Loading...**\n"
                "Preparing the channel controls."
            )
        )

        self.add_item(container)

    async def initialize(self):

        self.emojis["tickchecked"] = await get_application_emoji(
            self.bot,
            "tickchecked",
            "☑️",
        )

        self.emojis["tickunchecked"] = await get_application_emoji(
            self.bot,
            "tickunchecked",
            "⬜",
        )

        self.emojis["crosschecked"] = await get_application_emoji(
            self.bot,
            "crosschecked",
            "❌",
        )

        self.emojis["crossunchecked"] = await get_application_emoji(
            self.bot,
            "crossunchecked",
            "⬜",
        )

        # ----------------------------------------------------
        # Now that the emojis are loaded, build the actual UI.
        # ----------------------------------------------------

        self._build_page_one()

    async def _save_configuration(
        self,
        interaction: discord.Interaction,
    ):
        if not await self.interaction_check(interaction):
            return

        # ----------------------------------------------------
        # ONLY HERE do we write to MongoDB.
        # ----------------------------------------------------

        await set_channel_config(
            guild_id=self.guild_id,
            channel_id=self.channel.id,
            preset="custom",
            rules=self.rules,
            gif_domains=self.gif_domains,
            whitelist_users=list(self.whitelist_users),
            whitelist_roles=list(self.whitelist_roles),
        )

        result_view = discord.ui.LayoutView()

        result_view.add_item(
            discord.ui.TextDisplay(
                f"# ✅ Configuration Saved\n\n"
                f"Custom configuration saved for "
                f"{self.channel.mention}."
            )
        )

        await interaction.response.edit_message(
            view=result_view,
        )

        self.stop()

    async def cancel(
        self,
        interaction: discord.Interaction,
    ):

        # ----------------------------------------------------
        # Nothing was written to MongoDB.
        # Simply discard temporary state.
        # ----------------------------------------------------

        self.rules = DEFAULT_RULES.copy()
        self.gif_domains = DEFAULT_GIF_DOMAINS.copy()

        self.whitelist_users.clear()
        self.whitelist_roles.clear()

        result_view = discord.ui.LayoutView()

        result_view.add_item(
            discord.ui.TextDisplay(
                "# ↩️ Setup Rolled Back\n\nAll changes made during setup were discarded."
            )
        )

        await interaction.response.edit_message(
            view=result_view,
        )

        self.stop()
