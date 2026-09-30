import re

import discord
import emoji
from discord import app_commands
from discord.ext import commands

from app.database.repositories.channel_repository import (
    set_channel_config,
    get_channel_config,
)


CUSTOM_EMOJI_PATTERN = re.compile(
    r"<a?:[A-Za-z0-9_]+:\d+>"
)

GIF_DOMAINS = (
    "tenor.com",
    "media.tenor.com",
    "giphy.com",
    "media.giphy.com",
    "i.giphy.com",
)


def is_custom_emoji_only(content: str) -> bool:
    cleaned = CUSTOM_EMOJI_PATTERN.sub("", content).strip()
    return cleaned == "" and bool(CUSTOM_EMOJI_PATTERN.search(content))


def is_unicode_emoji_only(content: str) -> bool:
    cleaned = emoji.replace_emoji(content, replace="")
    return cleaned.strip() == "" and emoji.emoji_count(content) > 0


def is_gif_url(content: str) -> bool:
    urls = re.findall(r"https?://[^\s]+", content.lower())

    if not urls:
        return False

    return all(
        any(domain in url for domain in GIF_DOMAINS)
        for url in urls
    )


def is_gif_attachment(message: discord.Message) -> bool:
    for attachment in message.attachments:
        if attachment.content_type == "image/gif":
            return True

        if attachment.filename.lower().endswith(".gif"):
            return True

    return False


def is_gif_embed(message: discord.Message) -> bool:
    for embed in message.embeds:
        if embed.type == "gifv":
            return True

        embed_url = (embed.url or "").lower()

        if any(domain in embed_url for domain in GIF_DOMAINS):
            return True

    return False


def is_allowed_message(message: discord.Message) -> bool:
    content = message.content.strip()

    # GIF attachment
    if is_gif_attachment(message):
        return True

    # GIF embed, e.g. Discord GIF picker
    if is_gif_embed(message):
        return True

    # GIF URL
    if content and is_gif_url(content):
        return True

    # Custom Discord emoji
    if content and is_custom_emoji_only(content):
        return True

    # Unicode emoji
    if content and is_unicode_emoji_only(content):
        return True

    return False


class EmojiGif(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    channel = app_commands.Group(
        name="channel",
        description="Configure channel settings",
    )

    @channel.command(
        name="setup",
        description="Configure a channel with a preset",
    )
    @app_commands.default_permissions(manage_channels=True)
    @app_commands.describe(
        channel="The channel to configure",
        preset="The preset to apply",
    )
    @app_commands.choices(
        preset=[
            app_commands.Choice(
                name="Emoji + GIF",
                value="emoji_gif",
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

        await set_channel_config(
            guild_id=interaction.guild_id,
            channel_id=channel.id,
            preset=preset.value,
        )

        await interaction.response.send_message(
            f"Configured {channel.mention} with the `{preset.name}` preset.",
            ephemeral=True,
        )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        if message.guild is None:
            return

        config = await get_channel_config(
            guild_id=message.guild.id,
            channel_id=message.channel.id,
        )

        if not config:
            return

        if config["preset"] != "emoji_gif":
            return

        if not is_allowed_message(message):
            try:
                await message.delete()
            except discord.Forbidden:
                print(
                    f"Missing permission to delete message "
                    f"in #{message.channel.name}"
                )
            except discord.HTTPException:
                pass


async def setup(bot: commands.Bot):
    await bot.add_cog(EmojiGif(bot))