from __future__ import annotations

import re

import discord
from discord import app_commands

from app.services.translation_service import LANGUAGE_CODES

from .constants import SUPPORTED_LANGUAGES


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

    matches = [language for language in LANGUAGE_CODES if current in language.casefold()]

    return [app_commands.Choice(name=language, value=language) for language in matches[:25]]
