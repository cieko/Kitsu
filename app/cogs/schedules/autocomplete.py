from __future__ import annotations

import discord
from discord import app_commands

from app.database.repositories.schedule_repository import get_schedules


async def schedule_name_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    if interaction.guild_id is None:
        return []

    current = current.strip().casefold()
    choices = []

    # Offer "all" only for the delete command.
    if interaction.command and interaction.command.name == "delete":
        if not current or "all" in current:
            choices.append(app_commands.Choice(name="all", value="all"))

    schedules = await get_schedules(interaction.guild_id)

    for schedule in schedules:
        name = str(schedule.get("name", "Unnamed schedule"))

        if current in name.casefold():
            choices.append(
                app_commands.Choice(
                    name=name[:100],
                    value=name,
                )
            )

        if len(choices) >= 25:
            break

    return choices[:25]
