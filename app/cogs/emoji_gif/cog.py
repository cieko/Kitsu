from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from app.bot.permissions import GuildPermissionGroup
from app.database.repositories.channel_repository import (
    get_channel_config,
    reset_channel_config,
    set_channel_config,
)
from app.services.custom_channel_ui import CustomChannelView
from app.services.emoji_services import get_application_emoji

from .constants import DEFAULT_GIF_DOMAINS
from .detection import is_allowed_custom_message, is_allowed_emoji_gif_message


class EmojiGif(commands.Cog):
    def __init__(
        self,
        bot: commands.Bot,
    ):
        self.bot = bot

    # ========================================================
    # CHANNEL GROUP
    # ========================================================

    channel = GuildPermissionGroup(
        name="channel",
        description="Configure channel settings",
        permission="manage_channels",
        permission_label="Manage Channels",
    )

    # ========================================================
    # CHANNEL SETUP
    # ========================================================

    @channel.command(
        name="setup",
        description="Configure a channel with a preset",
    )
    @app_commands.describe(
        channel="The channel to configure",
        preset="The preset to apply",
    )
    @app_commands.choices(
        preset=[
            app_commands.Choice(
                name="𐔌՞ Emoji & GIF",
                value="emoji_gif",
            ),
            app_commands.Choice(
                name="𐔌՞ Custom",
                value="custom",
            ),
        ]
    )
    async def setup(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        preset: app_commands.Choice[str],
    ):

        if interaction.guild_id is None:
            await interaction.response.send_message(
                "This command can only be used inside a server.",
                ephemeral=True,
            )

            return

        # ----------------------------------------------------
        # CUSTOM CONFIGURATION
        # ----------------------------------------------------

        if preset.value == "custom":
            view = CustomChannelView(
                guild_id=interaction.guild_id,
                channel=channel,
                bot=interaction.client,
                owner_id=interaction.user.id,
            )

            # IMPORTANT:
            # Respond first, then initialize emojis.
            #
            # This prevents interaction timeout /
            # Unknown Interaction (10062).
            await interaction.response.send_message(
                view=view,
                ephemeral=True,
            )

            await view.initialize()

            await interaction.edit_original_response(
                view=view,
            )

            return

        # ----------------------------------------------------
        # NORMAL PRESET
        # ----------------------------------------------------

        await set_channel_config(
            guild_id=interaction.guild_id,
            channel_id=channel.id,
            preset=preset.value,
        )

        await interaction.response.send_message(
            f"Configured {channel.mention} with the `{preset.name}` preset.",
            ephemeral=True,
        )

    # ========================================================
    # CHANNEL RESET
    # ========================================================

    @channel.command(
        name="reset",
        description="Reset channel settings",
    )
    @app_commands.describe(
        channel="The channel to reset",
    )
    async def reset(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
    ):

        if interaction.guild_id is None:
            await interaction.response.send_message(
                "This command can only be used inside a server.",
                ephemeral=True,
            )

            return

        await reset_channel_config(
            guild_id=interaction.guild_id,
            channel_id=channel.id,
        )

        await interaction.response.send_message(
            f"Reset settings for {channel.mention}.",
            ephemeral=True,
        )

    # ========================================================
    # MESSAGE LISTENER
    # ========================================================

    @commands.Cog.listener()
    async def on_message(
        self,
        message: discord.Message,
    ):

        # ----------------------------------------------------
        # Ignore bots
        # ----------------------------------------------------

        if message.author.bot:
            return

        # ----------------------------------------------------
        # Ignore DMs
        # ----------------------------------------------------

        if message.guild is None:
            return

        # ----------------------------------------------------
        # Get channel configuration
        # ----------------------------------------------------

        config = await get_channel_config(
            guild_id=message.guild.id,
            channel_id=message.channel.id,
        )

        if not config:
            return

        preset = config.get(
            "preset",
        )

        # ----------------------------------------------------
        # Whitelist
        #
        # Whitelisted members and roles bypass restrictions.
        # ----------------------------------------------------

        if preset == "custom":
            whitelist_users = set(
                config.get(
                    "whitelist_users",
                    [],
                )
            )

            whitelist_roles = set(
                config.get(
                    "whitelist_roles",
                    [],
                )
            )

            if message.author.id in whitelist_users:
                return

            member_role_ids = {role.id for role in message.author.roles}

            if member_role_ids & whitelist_roles:
                return

        # ----------------------------------------------------
        # Check message
        # ----------------------------------------------------

        if preset == "emoji_gif":
            allowed = is_allowed_emoji_gif_message(
                message,
            )

        elif preset == "custom":
            rules = config.get(
                "rules",
                {},
            )

            gif_domains = config.get(
                "gif_domains",
                list(DEFAULT_GIF_DOMAINS),
            )

            allowed = is_allowed_custom_message(
                message,
                rules,
                gif_domains,
            )

        else:
            return

        # ----------------------------------------------------
        # Message is allowed
        # ----------------------------------------------------

        if allowed:
            return

        # ----------------------------------------------------
        # Message is NOT allowed
        # ----------------------------------------------------

        try:
            warning_emoji = await get_application_emoji(
                self.bot,
                "warning",
                "⚠️",
            )

            await message.delete()

            if preset == "custom":
                await message.channel.send(
                    f"-# {warning_emoji} "
                    "This content isn't allowed "
                    "in this channel. "
                    f"{message.author.mention}",
                    delete_after=5,
                )

            else:
                await message.channel.send(
                    f"-# {warning_emoji} "
                    "This is an **Emoji + GIF** channel only. "
                    "Please send emojis or GIFs. "
                    f"{message.author.mention}",
                    delete_after=5,
                )

        except discord.Forbidden:
            print(f"Missing permission to delete message in #{message.channel.name}")

        except discord.HTTPException:
            pass


async def setup(
    bot: commands.Bot,
):

    await bot.add_cog(EmojiGif(bot))
