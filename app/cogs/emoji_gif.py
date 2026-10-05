import re

import discord
from discord import app_commands
from discord.ext import commands

from app.database.repositories.channel_repository import (
    get_channel_config,
    reset_channel_config,
    set_channel_config,
)
from app.services.custom_channel_ui import CustomChannelView
from app.services.emoji_services import get_application_emoji


# ============================================================
# CONSTANTS
# ============================================================

CUSTOM_EMOJI_PATTERN = re.compile(
    r"<a?:[A-Za-z0-9_]+:\d+>"
)

DEFAULT_GIF_DOMAINS = (
    "tenor.com",
    "media.tenor.com",
    "giphy.com",
    "media.giphy.com",
    "i.giphy.com",
)


# ============================================================
# CONTENT DETECTION
# ============================================================

def contains_custom_emoji(content: str) -> bool:
    return bool(
        CUSTOM_EMOJI_PATTERN.search(content)
    )


def is_custom_emoji_only(content: str) -> bool:
    cleaned = CUSTOM_EMOJI_PATTERN.sub(
        "",
        content,
    ).strip()

    return (
        bool(CUSTOM_EMOJI_PATTERN.search(content))
        and not cleaned
    )


def is_gif_url(content: str) -> bool:
    urls = re.findall(
        r"https?://[^\s]+",
        content.lower(),
    )

    if not urls:
        return False

    return all(
        url.endswith(".gif")
        or ".gif?" in url
        for url in urls
    )


def is_gif_attachment(
    message: discord.Message,
) -> bool:

    for attachment in message.attachments:

        content_type = (
            attachment.content_type or ""
        ).lower()

        if content_type == "image/gif":
            return True

        if attachment.filename.lower().endswith(".gif"):
            return True

    return False


def is_image_attachment(
    message: discord.Message,
) -> bool:

    for attachment in message.attachments:

        content_type = (
            attachment.content_type or ""
        ).lower()

        if content_type.startswith("image/"):
            return True

        if attachment.filename.lower().endswith(
            (
                ".png",
                ".jpg",
                ".jpeg",
                ".webp",
                ".bmp",
            )
        ):
            return True

    return False


def is_video_attachment(
    message: discord.Message,
) -> bool:

    for attachment in message.attachments:

        content_type = (
            attachment.content_type or ""
        ).lower()

        if content_type.startswith("video/"):
            return True

        if attachment.filename.lower().endswith(
            (
                ".mp4",
                ".mov",
                ".webm",
                ".mkv",
                ".avi",
            )
        ):
            return True

    return False


def has_link(content: str) -> bool:
    return bool(
        re.search(
            r"https?://[^\s]+",
            content,
            re.IGNORECASE,
        )
    )


def is_gif_embed(
    message: discord.Message,
    gif_domains: list[str] | tuple[str, ...],
) -> bool:

    for embed in message.embeds:

        # Discord identified this embed as a GIF.
        if embed.type == "gifv":
            return True

        embed_url = (
            embed.url or ""
        ).lower()

        # "*" means GIFs from any domain are allowed.
        if "*" in gif_domains:
            return True

        if any(
            domain in embed_url
            for domain in gif_domains
        ):
            return True

    return False


# ============================================================
# CUSTOM MESSAGE RULES
# ============================================================

def is_allowed_custom_message(
    message: discord.Message,
    rules: dict,
    gif_domains: list[str] | tuple[str, ...],
) -> bool:

    content = message.content.strip()

    # --------------------------------------------------------
    # Stickers
    # --------------------------------------------------------

    if message.stickers:

        return rules.get(
            "stickers",
            False,
        )

    # --------------------------------------------------------
    # GIF attachments / embeds
    # --------------------------------------------------------

    if (
        is_gif_attachment(message)
        or is_gif_embed(
            message,
            gif_domains,
        )
    ):

        return rules.get(
            "gif",
            False,
        )

    # --------------------------------------------------------
    # Other attachments
    # --------------------------------------------------------

    if message.attachments:

        # GIF was already checked above.

        if is_image_attachment(message):

            return rules.get(
                "images",
                False,
            )

        if is_video_attachment(message):

            return rules.get(
                "videos",
                False,
            )

        # Generic files are not configurable,
        # so reject them.
        return False

    # --------------------------------------------------------
    # Links / GIF URLs
    # --------------------------------------------------------

    if content and has_link(content):

        urls = re.findall(
            r"https?://[^\s]+",
            content.lower(),
        )

        # GIF URL
        if is_gif_url(content):

            if not rules.get(
                "gif",
                False,
            ):
                return False

            if "*" in gif_domains:
                return True

            return all(
                any(
                    domain in url
                    for domain in gif_domains
                )
                for url in urls
            )

        # Normal link
        return rules.get(
            "links",
            False,
        )

    # --------------------------------------------------------
    # Discord custom emoji
    # --------------------------------------------------------

    if is_custom_emoji_only(content):

        return rules.get(
            "custom_emoji",
            False,
        )

    # --------------------------------------------------------
    # Text
    #
    # Unicode emoji are considered text.
    #
    # Example:
    #   hello
    #   hello 😂
    #   😂
    #
    # All of these are controlled by "text".
    # --------------------------------------------------------

    if content:

        return rules.get(
            "text",
            False,
        )

    # --------------------------------------------------------
    # Empty / unknown content
    # --------------------------------------------------------

    return False


# ============================================================
# EMOJI + GIF PRESET
# ============================================================

def is_allowed_emoji_gif_message(
    message: discord.Message,
) -> bool:

    content = message.content.strip()

    # Stickers
    if message.stickers:
        return True

    # GIF attachment
    if is_gif_attachment(message):
        return True

    # GIF embed
    if is_gif_embed(
        message,
        DEFAULT_GIF_DOMAINS,
    ):
        return True

    # GIF URL
    if content and is_gif_url(content):
        return True

    # Discord custom emoji
    if content and is_custom_emoji_only(content):
        return True

    # Unicode emoji
    #
    # Unicode emoji are allowed by the preset.
    #
    # We intentionally don't need a separate
    # "unicode_emoji" rule anymore.
    if content:
        import emoji

        if (
            emoji.emoji_count(content) > 0
            and not emoji.replace_emoji(
                content,
                replace="",
            ).strip()
        ):
            return True

    return False


# ============================================================
# COG
# ============================================================

class EmojiGif(commands.Cog):

    def __init__(
        self,
        bot: commands.Bot,
    ):
        self.bot = bot

    # ========================================================
    # CHANNEL GROUP
    # ========================================================

    channel = app_commands.Group(
        name="channel",
        description="Configure channel settings",
    )

    # ========================================================
    # CHANNEL SETUP
    # ========================================================

    @channel.command(
        name="setup",
        description="Configure a channel with a preset",
    )
    @app_commands.default_permissions(
        manage_channels=True,
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
            f"Configured {channel.mention} with the "
            f"`{preset.name}` preset.",
            ephemeral=True,
        )

    # ========================================================
    # CHANNEL RESET
    # ========================================================

    @channel.command(
        name="reset",
        description="Reset channel settings",
    )
    @app_commands.default_permissions(
        manage_channels=True,
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

            member_role_ids = {
                role.id
                for role in message.author.roles
            }

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

            print(
                f"Missing permission to delete message "
                f"in #{message.channel.name}"
            )

        except discord.HTTPException:

            pass


# ============================================================
# EXTENSION SETUP
# ============================================================

async def setup(
    bot: commands.Bot,
):

    await bot.add_cog(
        EmojiGif(bot)
    )
