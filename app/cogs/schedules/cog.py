from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import discord
from discord import app_commands
from discord.ext import commands

from app.database.repositories.schedule_repository import (
    add_schedule,
    get_schedules,
    get_timezone,
    set_timezone,
)
from app.services.schedule_service import parse_message_script

from .management import ScheduleManagement
from .views import ScheduleListView


class Schedules(ScheduleManagement, commands.GroupCog, group_name="schedule"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def interaction_check(
        self,
        interaction: discord.Interaction,
    ) -> bool:
        if interaction.guild is None:
            await interaction.response.send_message(
                "This command can only be used in a server.",
                ephemeral=True,
            )
            return False

        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "You need Administrator permissions to manage schedules.",
                ephemeral=True,
            )
            return False

        return True

    @app_commands.command(
        name="timezone",
        description="Set the timezone used by scheduled messages",
    )
    @app_commands.describe(
        timezone="Timezone name, for example Asia/Kolkata",
    )
    async def timezone_command(
        self,
        interaction: discord.Interaction,
        timezone: str,
    ):
        await interaction.response.defer(ephemeral=True, thinking=True)

        try:
            ZoneInfo(timezone)
        except (ZoneInfoNotFoundError, ValueError):
            await interaction.edit_original_response(
                content=(
                    "Invalid timezone. Use an IANA timezone such as "
                    "`Asia/Kolkata`, `Europe/London`, or `UTC`."
                )
            )
            return

        await set_timezone(interaction.guild_id, timezone)
        saved_timezone = await get_timezone(interaction.guild_id)

        await interaction.edit_original_response(
            content=(f"Requested timezone: `{timezone}`\nSaved timezone: `{saved_timezone}`")
        )

    @app_commands.command(
        name="add",
        description="Create a scheduled message",
    )
    @app_commands.describe(
        name="A unique name for this schedule",
        channel="Channel where the message will be sent",
        time="Time in HH:MM format using the server timezone",
        message="Message script, including optional embed directives",
    )
    async def add_command(
        self,
        interaction: discord.Interaction,
        name: str,
        channel: discord.TextChannel,
        time: str,
        message: str,
    ):
        await interaction.response.defer(ephemeral=True, thinking=True)

        name = name.strip()
        message = message.strip()

        if not name:
            await interaction.edit_original_response(content="Please provide a schedule name.")
            return

        if len(name) > 100:
            await interaction.edit_original_response(
                content="The schedule name cannot exceed 100 characters."
            )
            return

        try:
            datetime.strptime(time, "%H:%M")
        except ValueError:
            await interaction.edit_original_response(
                content="Invalid time. Use 24-hour `HH:MM` format, such as `18:30`."
            )
            return

        existing_schedules = await get_schedules(interaction.guild_id)

        if any(
            str(item.get("name", "")).casefold() == name.casefold() for item in existing_schedules
        ):
            await interaction.edit_original_response(
                content=f"A schedule named **{name}** already exists."
            )
            return

        if not message:
            await interaction.edit_original_response(content="The message script cannot be empty.")
            return

        if len(message) > 4000:
            await interaction.edit_original_response(
                content="The message script cannot exceed 4000 characters."
            )
            return

        await add_schedule(
            guild_id=interaction.guild_id,
            name=name,
            channel_id=channel.id,
            time=time,
            message=message,
            created_by=interaction.user.id,
        )

        embed = discord.Embed(
            title="Schedule Created",
            description=(
                f"**Name:** {name}\n"
                f"**Channel:** {channel.mention}\n"
                f"**Time:** `{time}`\n"
                f"**Timezone:** `{await get_timezone(interaction.guild_id)}`"
            ),
            color=discord.Color.from_rgb(250, 192, 228),
        )

        try:
            preview_content, preview_embed = parse_message_script(message)

            if preview_content:
                embed.add_field(
                    name="Message Preview",
                    value=str(preview_content)[:1024],
                    inline=False,
                )

            await interaction.edit_original_response(
                content="Schedule created successfully.",
                embed=embed,
                allowed_mentions=discord.AllowedMentions.none(),
            )

            if isinstance(preview_embed, discord.Embed):
                await interaction.followup.send(
                    embed=preview_embed,
                    ephemeral=True,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            return

        except Exception:
            # The schedule has already been saved.
            pass

        await interaction.edit_original_response(
            content="Schedule created successfully.",
            embed=embed,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(
        name="list",
        description="List scheduled messages with pagination",
    )
    async def list_command(
        self,
        interaction: discord.Interaction,
    ):
        await interaction.response.defer(ephemeral=True, thinking=True)

        guild = interaction.guild
        guild_id = interaction.guild_id

        if guild is None or guild_id is None:
            await interaction.edit_original_response(
                content="This command can only be used in a server.",
                embed=None,
                view=None,
            )
            return

        schedules = await get_schedules(guild_id)
        timezone = await get_timezone(guild_id)

        if not schedules:
            await interaction.edit_original_response(
                content="No scheduled messages configured for this server.",
                embed=None,
                view=None,
            )
            return

        view = ScheduleListView(
            owner_id=interaction.user.id,
            guild=guild,
            schedules=schedules,
            timezone=timezone,
        )

        await interaction.edit_original_response(
            content=None,
            embed=view.create_embed(),
            view=view,
            allowed_mentions=discord.AllowedMentions.none(),
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Schedules(bot))
