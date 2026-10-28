from __future__ import annotations

import asyncio

import discord
from discord import app_commands
from discord.ext import commands

from app.database.session import db
from app.services.translation_service import TranslationService, detect_language

from .commands import TranslationCommands
from .configuration import TranslationConfiguration
from .constants import (
    CONFIG_COLLECTION,
    MAX_INPUT_LENGTH,
    MAX_TRANSLATION_LENGTH,
    logger,
)
from .groups import translation
from .helpers import clean_message, language_autocomplete, resolve_language


class TranslationCog(TranslationCommands, TranslationConfiguration, commands.Cog):
    translation = translation

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

    async def _translate(
        self,
        text: str,
        target_language: str,
    ) -> str | None:
        text = clean_message(text)

        if not text:
            return None

        if len(text) > MAX_INPUT_LENGTH:
            raise ValueError(f"Text must be {MAX_INPUT_LENGTH} characters or fewer.")

        detection = detect_language(text)

        result = await self.translator.translate(
            text,
            target_language,
            detection=detection,
        )

        if result is None:
            return None

        return result.translated_text

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

            if clean_message(translated).casefold() == clean_message(message.content).casefold():
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
