
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from app.database.session import db
from app.services.translation_service import (
    LANGUAGE_CODES,
    TranslationService,
    detect_language,
)

logger = logging.getLogger(__name__)

CONFIG_COLLECTION = "translation_channel_configs"
MAX_TRANSLATION_LENGTH = 1900
MAX_INPUT_LENGTH = 5000

# MongoDB uses these values to distinguish configuration scopes.
SERVER_DEFAULT_ID = "server-default"

SUPPORTED_LANGUAGES = {
    language.casefold(): language
    for language in LANGUAGE_CODES
}


def resolve_language(language: str) -> str | None:
    """Resolve a supported language name, ignoring letter case."""
    return SUPPORTED_LANGUAGES.get(language.strip().casefold())


def clean_message(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


async def language_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    """Suggest supported languages while the user types."""
    current = current.strip().casefold()

    matches = [
        language
        for language in LANGUAGE_CODES
        if current in language.casefold()
    ]

    return [
        app_commands.Choice(name=language, value=language)
        for language in matches[:25]
    ]


class TranslationCog(commands.Cog):
    translation = app_commands.Group(
        name="translation",
        description="Configure automatic message translations.",
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.translator = TranslationService()
        self.configs = db[CONFIG_COLLECTION]

    async def cog_load(self) -> None:
        await asyncio.to_thread(
            self.configs.create_index,
            [("guild_id", 1), ("channel_id", 1)],
            unique=True,
        )

    # ---------------------------------------------------------
    # MongoDB configuration helpers
    # ---------------------------------------------------------

    async def _get_config(
        self,
        guild_id: int,
        channel_id: int | str,
    ) -> dict[str, Any] | None:
        return await asyncio.to_thread(
            self.configs.find_one,
            {
                "guild_id": str(guild_id),
                "channel_id": str(channel_id),
            },
        )

    async def _save_config(
        self,
        guild_id: int,
        channel_id: int | str,
        target_language: str,
        *,
        scope: str,
    ) -> None:
        await asyncio.to_thread(
            self.configs.update_one,
            {
                "guild_id": str(guild_id),
                "channel_id": str(channel_id),
            },
            {
                "$set": {
                    "guild_id": str(guild_id),
                    "channel_id": str(channel_id),
                    "target_language": target_language,
                    "scope": scope,
                    "enabled": True,
                }
            },
            upsert=True,
        )

    async def _disable_config(
        self,
        guild_id: int,
        channel_id: int | str,
    ) -> None:
        # Keep the document: disabled settings deliberately block inheritance.
        await asyncio.to_thread(
            self.configs.update_one,
            {
                "guild_id": str(guild_id),
                "channel_id": str(channel_id),
            },
            {
                "$set": {
                    "enabled": False,
                }
            },
        )

    async def _reset_config(
        self,
        guild_id: int,
        channel_id: int | str,
    ) -> None:
        # Deleting an override allows the next level to take effect.
        await asyncio.to_thread(
            self.configs.delete_one,
            {
                "guild_id": str(guild_id),
                "channel_id": str(channel_id),
            },
        )

    async def _resolve_config(
        self,
        message: discord.Message,
    ) -> dict[str, Any] | None:
        """Resolve thread > parent channel > server default."""

        guild_id = message.guild.id
        channel = message.channel

        # A thread's own setting takes precedence, including disabled settings.
        if isinstance(channel, discord.Thread):
            config = await self._get_config(guild_id, channel.id)

            if config is not None:
                return config

            # If there is no thread override, inherit from the parent.
            if channel.parent_id is not None:
                config = await self._get_config(
                    guild_id,
                    channel.parent_id,
                )

                if config is not None:
                    return config

        else:
            config = await self._get_config(guild_id, channel.id)

            if config is not None:
                return config

        # Final fallback: server-wide default.
        return await self._get_config(guild_id, SERVER_DEFAULT_ID)

    # ---------------------------------------------------------
    # Translation helper
    # ---------------------------------------------------------

    async def _translate(
        self,
        text: str,
        target_language: str,
    ) -> str | None:
        text = clean_message(text)

        if not text:
            return None

        if len(text) > MAX_INPUT_LENGTH:
            raise ValueError(
                f"Text must be {MAX_INPUT_LENGTH} characters or fewer."
            )

        detection = detect_language(text)

        result = await self.translator.translate(
            text,
            target_language,
            detection=detection,
        )

        if result is None:
            return None

        return result.translated_text

    # ---------------------------------------------------------
    # Manual translation
    # /translate text:<text> target_language:<language>
    # ---------------------------------------------------------

    @app_commands.command(
        name="translate",
        description="Translate text into a supported language.",
    )
    @app_commands.describe(
        text="The text you want to translate.",
        target_language="Choose or type the target language.",
    )
    @app_commands.autocomplete(target_language=language_autocomplete)
    async def translate_command(
        self,
        interaction: discord.Interaction,
        text: str,
        target_language: str,
    ) -> None:
        language = resolve_language(target_language)

        if language is None:
            await interaction.response.send_message(
                "Choose a language from the autocomplete suggestions.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(thinking=True)

        try:
            translated = await self._translate(text, language)

            if not translated:
                await interaction.edit_original_response(
                    content=(
                        "I couldn't confidently translate that text. "
                        "Try a longer message or check the source language."
                    )
                )
                return

            await interaction.edit_original_response(
                content=(
                    f"**Translation · "
                    f"{discord.utils.escape_markdown(language)}**\n"
                    f"{translated[:MAX_TRANSLATION_LENGTH]}"
                )
            )

        except ValueError as exc:
            await interaction.edit_original_response(content=str(exc))

        except Exception:
            logger.exception("Manual translation failed.")
            await interaction.edit_original_response(
                content="Translation failed. Check the bot logs."
            )

    # ---------------------------------------------------------
    # Server default
    # /translation set-server-default
    # /translation reset-server-default
    # ---------------------------------------------------------

    @translation.command(
        name="set-server-default",
        description="Set the default translation language for this server.",
    )
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(
        target_language="Default language for channels without an override.",
    )
    @app_commands.autocomplete(target_language=language_autocomplete)
    async def set_server_default(
        self,
        interaction: discord.Interaction,
        target_language: str,
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "Use this command inside a server.",
                ephemeral=True,
            )
            return

        language = resolve_language(target_language)

        if language is None:
            await interaction.response.send_message(
                "Choose a supported language from the suggestions.",
                ephemeral=True,
            )
            return

        await self._save_config(
            interaction.guild.id,
            SERVER_DEFAULT_ID,
            language,
            scope="server",
        )

        await interaction.response.send_message(
            f"Server default language set to **{language}**.",
            ephemeral=True,
        )

    @translation.command(
        name="reset-server-default",
        description="Remove the server-wide default language.",
    )
    @app_commands.default_permissions(manage_guild=True)
    async def reset_server_default(
        self,
        interaction: discord.Interaction,
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "Use this command inside a server.",
                ephemeral=True,
            )
            return

        await self._reset_config(
            interaction.guild.id,
            SERVER_DEFAULT_ID,
        )

        await interaction.response.send_message(
            "The server default was removed. Only explicit channel "
            "and thread settings will apply.",
            ephemeral=True,
        )

    # ---------------------------------------------------------
    # Channel settings
    # /translation set-channel
    # /translation disable-channel
    # /translation reset-channel
    # ---------------------------------------------------------

    @translation.command(
        name="set-channel",
        description="Set the translation language for a channel.",
    )
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(
        channel="The channel to configure.",
        target_language="Language to translate messages into.",
    )
    @app_commands.autocomplete(target_language=language_autocomplete)
    async def set_channel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        target_language: str,
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "Use this command inside a server.",
                ephemeral=True,
            )
            return

        language = resolve_language(target_language)

        if language is None:
            await interaction.response.send_message(
                "Choose a supported language from the suggestions.",
                ephemeral=True,
            )
            return

        await self._save_config(
            interaction.guild.id,
            channel.id,
            language,
            scope="channel",
        )

        await interaction.response.send_message(
            f"Automatic translation in {channel.mention} is now set "
            f"to **{language}**.",
            ephemeral=True,
        )

    @translation.command(
        name="disable-channel",
        description="Stop automatic translation in a channel.",
    )
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(channel="The channel to disable.")
    async def disable_channel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "Use this command inside a server.",
                ephemeral=True,
            )
            return

        await self._disable_config(
            interaction.guild.id,
            channel.id,
        )

        await interaction.response.send_message(
            f"Automatic translation is disabled in {channel.mention}. "
            "This channel will not inherit the server default. "
            "Use reset-channel to restore inheritance.",
            ephemeral=True,
        )

    @translation.command(
        name="reset-channel",
        description="Remove a channel override and inherit its default.",
    )
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(channel="The channel whose override to remove.")
    async def reset_channel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "Use this command inside a server.",
                ephemeral=True,
            )
            return

        await self._reset_config(
            interaction.guild.id,
            channel.id,
        )

        await interaction.response.send_message(
            f"The translation override for {channel.mention} was removed. "
            "It will inherit the server default unless a thread overrides it.",
            ephemeral=True,
        )

    # ---------------------------------------------------------
    # Thread settings
    # Run these commands inside the thread to configure it.
    # /translation set-thread
    # /translation disable-thread
    # /translation reset-thread
    # ---------------------------------------------------------

    @translation.command(
        name="set-thread",
        description="Set the translation language for the current thread.",
    )
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(
        target_language="Language to translate messages into in this thread.",
    )
    @app_commands.autocomplete(target_language=language_autocomplete)
    async def set_thread(
        self,
        interaction: discord.Interaction,
        target_language: str,
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "Use this command inside a server thread.",
                ephemeral=True,
            )
            return

        if not isinstance(interaction.channel, discord.Thread):
            await interaction.response.send_message(
                "Run this command inside the thread you want to configure.",
                ephemeral=True,
            )
            return

        language = resolve_language(target_language)

        if language is None:
            await interaction.response.send_message(
                "Choose a supported language from the suggestions.",
                ephemeral=True,
            )
            return

        await self._save_config(
            interaction.guild.id,
            interaction.channel.id,
            language,
            scope="thread",
        )

        await interaction.response.send_message(
            f"This thread's translation language is now **{language}**. "
            "It overrides the parent channel's setting.",
            ephemeral=True,
        )

    @translation.command(
        name="disable-thread",
        description="Disable automatic translation in the current thread.",
    )
    @app_commands.default_permissions(manage_guild=True)
    async def disable_thread(
        self,
        interaction: discord.Interaction,
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "Use this command inside a server thread.",
                ephemeral=True,
            )
            return

        if not isinstance(interaction.channel, discord.Thread):
            await interaction.response.send_message(
                "Run this command inside the thread you want to disable.",
                ephemeral=True,
            )
            return

        await self._disable_config(
            interaction.guild.id,
            interaction.channel.id,
        )

        await interaction.response.send_message(
            "Automatic translation is disabled in this thread, "
            "even if the parent channel has translation enabled. "
            "Use reset-thread to restore inheritance.",
            ephemeral=True,
        )

    @translation.command(
        name="reset-thread",
        description="Remove this thread's override and inherit its parent.",
    )
    @app_commands.default_permissions(manage_guild=True)
    async def reset_thread(
        self,
        interaction: discord.Interaction,
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "Use this command inside a server thread.",
                ephemeral=True,
            )
            return

        if not isinstance(interaction.channel, discord.Thread):
            await interaction.response.send_message(
                "Run this command inside the thread you want to reset.",
                ephemeral=True,
            )
            return

        await self._reset_config(
            interaction.guild.id,
            interaction.channel.id,
        )

        await interaction.response.send_message(
            "This thread's override was removed. It will inherit its "
            "parent channel's setting, or the server default if needed.",
            ephemeral=True,
        )

    # ---------------------------------------------------------
    # Automatic translation
    # ---------------------------------------------------------

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
            return

        if not message.content or not message.content.strip():
            return

        # Ignore bot commands.
        if message.content.startswith((",", "/", "!")):
            return

        try:
            config = await self._resolve_config(message)

            if config is None or not config.get("enabled", False):
                return

            target_language = config.get("target_language")

            if not target_language:
                return

            translated = await self._translate(
                message.content,
                target_language,
            )

            if not translated:
                return

            if clean_message(translated).casefold() == clean_message(
                message.content
            ).casefold():
                return

            await message.reply(
                content=(
                    f"**Translation · "
                    f"{discord.utils.escape_markdown(target_language)}**\n"
                    f"{translated[:MAX_TRANSLATION_LENGTH]}"
                ),
                mention_author=False,
                allowed_mentions=discord.AllowedMentions.none(),
            )

        except Exception:
            logger.exception(
                "Automatic translation failed for message %s.",
                message.id,
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TranslationCog(bot))
