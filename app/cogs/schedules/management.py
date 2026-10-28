from __future__ import annotations

import discord
from discord import app_commands

from app.database.repositories.schedule_repository import (
    delete_schedule,
    get_schedule,
    get_schedules,
    set_schedule_active,
)

from .autocomplete import schedule_name_autocomplete
from .views import DeleteAllSchedulesView, EditScheduleModal


class ScheduleManagement:
    @app_commands.command(
        name="delete",
        description="Delete a scheduled message or all schedules",
    )
    @app_commands.describe(
        name="Choose a schedule name or 'all' to delete every schedule",
    )
    @app_commands.autocomplete(name=schedule_name_autocomplete)
    async def delete_command(
        self,
        interaction: discord.Interaction,
        name: str,
    ):
        await interaction.response.defer(ephemeral=True, thinking=True)

        guild_id = interaction.guild_id

        if guild_id is None:
            await interaction.edit_original_response(
                content="This command can only be used in a server."
            )
            return

        if name.strip().casefold() == "all":
            schedules = await get_schedules(guild_id)

            if not schedules:
                await interaction.edit_original_response(
                    content="There are no schedules to delete."
                )
                return

            view = DeleteAllSchedulesView(
                owner_id=interaction.user.id,
                guild_id=guild_id,
            )

            await interaction.edit_original_response(
                content=(
                    f"Are you sure you want to delete **all "
                    f"{len(schedules)} scheduled message(s)** in this server? "
                    "This action cannot be undone."
                ),
                view=view,
            )
            return

        schedule = await get_schedule(guild_id, name)

        if not schedule:
            await interaction.edit_original_response(
                content=f"No schedule named **{name}** was found."
            )
            return

        await delete_schedule(guild_id, name)

        await interaction.edit_original_response(
            content=f"Deleted schedule **{name}**.",
            view=None,
        )

    @app_commands.command(
        name="edit",
        description="Edit the message of a scheduled message",
    )
    @app_commands.describe(name="Name of the schedule to edit")
    @app_commands.autocomplete(name=schedule_name_autocomplete)
    async def edit_command(
        self,
        interaction: discord.Interaction,
        name: str,
    ):
        schedule = await get_schedule(interaction.guild_id, name)

        if not schedule:
            await interaction.response.send_message(
                f"No schedule named **{name}** was found.",
                ephemeral=True,
            )
            return

        modal = EditScheduleModal(
            schedule_name=name,
            current_message=str(schedule.get("message", "")),
        )

        await interaction.response.send_modal(modal)

    @app_commands.command(
        name="toggle",
        description="Activate or pause a scheduled message",
    )
    @app_commands.describe(name="Name of the schedule to toggle")
    @app_commands.autocomplete(name=schedule_name_autocomplete)
    async def toggle_command(
        self,
        interaction: discord.Interaction,
        name: str,
    ):
        await interaction.response.defer(ephemeral=True, thinking=True)

        guild_id = interaction.guild_id

        if guild_id is None:
            await interaction.edit_original_response(
                content="This command can only be used in a server."
            )
            return

        schedule = await get_schedule(guild_id, name)

        if not schedule:
            await interaction.edit_original_response(
                content=f"No schedule named **{name}** was found."
            )
            return

        new_active_state = not schedule.get("active", True)

        await set_schedule_active(
            guild_id,
            name,
            new_active_state,
        )

        status = "activated" if new_active_state else "paused"

        await interaction.edit_original_response(content=f"Schedule **{name}** has been {status}.")
