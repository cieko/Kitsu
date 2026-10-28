from __future__ import annotations

import discord
from discord import app_commands

from .constants import SERVER_DEFAULT_ID
from .groups import translation
from .helpers import language_autocomplete, resolve_language


class TranslationCommands:
    @translation.command(
        name="set-server-default",
        description="Set the default translation language for this server.",
    )
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
            "The server default was removed. Only explicit channel and thread settings will apply.",
            ephemeral=True,
        )

    @translation.command(
        name="set-channel",
        description="Set the translation language for a channel.",
    )
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
            f"Automatic translation in {channel.mention} is now set to **{language}**.",
            ephemeral=True,
        )

    @translation.command(
        name="disable-channel",
        description="Stop automatic translation in a channel.",
    )
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

    @translation.command(
        name="set-thread",
        description="Set the translation language for the current thread.",
    )
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
