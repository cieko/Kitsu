from __future__ import annotations

import discord

from app.services.emoji_services import get_application_emoji

from .market_settings import show_settings


async def show_settings_menu(
    self,
    interaction: discord.Interaction,
) -> None:
    """Show the Ghosty settings menu to server members."""

    if interaction.guild is None:
        await interaction.response.send_message(
            "Use these settings inside a server.",
            ephemeral=True,
        )
        return

    new_emoji = await get_application_emoji(
        self.bot,
        "new",
        "➕",
    )

    gear_emoji = await get_application_emoji(
        self.bot,
        "gear",
        "⚙️",
    )

    embed = discord.Embed(
        title=f"{gear_emoji} Ghosty Settings",
        description=(
            "Manage your Ghosty experience using the options below.\n\n"
            f"{new_emoji} **Create Temporary Channel**\n"
            "Create your own temporary channel for conversations, "
            "gaming, or hanging out with other members. Choose a channel "
            "name and how long it should stay active. "
            "The channel will be deleted automatically when its time expires."
        ),
        color=discord.Colour.dark_purple(),
    )

    menu = discord.ui.View(timeout=300)

    create_button = discord.ui.Button(
        label="Create Temporary Channel",
        style=discord.ButtonStyle.success,
        emoji=new_emoji,
    )

    async def create_temp_channel(
        button_interaction: discord.Interaction,
    ) -> None:
        await self._show_tempchannel_creator(button_interaction)

    create_button.callback = create_temp_channel
    menu.add_item(create_button)

    permissions = interaction.user.guild_permissions

    if permissions.administrator or permissions.manage_guild:
        market_button = discord.ui.Button(
            label="",
            style=discord.ButtonStyle.secondary,
            emoji=gear_emoji,
        )

        async def open_market_settings(
            button_interaction: discord.Interaction,
        ) -> None:
            await show_settings(self, button_interaction)

        market_button.callback = open_market_settings
        menu.add_item(market_button)

    await interaction.response.send_message(
        embed=embed,
        view=menu,
        ephemeral=True,
    )
