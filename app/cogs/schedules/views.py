from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import discord

from app.database.repositories.schedule_repository import (
    delete_all_schedules,
    get_schedule,
    update_schedule_message,
)


class DeleteAllSchedulesView(discord.ui.View):
    def __init__(self, owner_id: int, guild_id: int):
        super().__init__(timeout=60)
        self.owner_id = owner_id
        self.guild_id = guild_id

    async def interaction_check(
        self,
        interaction: discord.Interaction,
    ) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "Only the administrator who started this action can confirm it.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(
        label="Delete All",
        style=discord.ButtonStyle.danger,
    )
    async def confirm_delete(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        await interaction.response.defer()

        deleted_count = await delete_all_schedules(self.guild_id)

        self.stop()

        await interaction.edit_original_response(
            content=f"Deleted {deleted_count} scheduled message(s) from this server.",
            view=None,
        )

    @discord.ui.button(
        label="Cancel",
        style=discord.ButtonStyle.secondary,
    )
    async def cancel_delete(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        self.stop()

        await interaction.response.edit_message(
            content="Delete-all action cancelled.",
            embed=None,
            view=None,
        )


class EditScheduleModal(discord.ui.Modal):
    def __init__(self, schedule_name: str, current_message: str):
        super().__init__(title="Edit Scheduled Message")
        self.schedule_name = schedule_name

        self.message_input = discord.ui.TextInput(
            label="Message script",
            style=discord.TextStyle.paragraph,
            default=current_message[:4000],
            max_length=4000,
            required=True,
            placeholder=("{embed}\n{color: #FAC0E4}\n{description: Your message here}"),
        )
        self.add_item(self.message_input)

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.guild_id is None:
            await interaction.response.send_message(
                "This command can only be used in a server.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        schedule = await get_schedule(
            interaction.guild_id,
            self.schedule_name,
        )

        if not schedule:
            await interaction.edit_original_response(content="Schedule not found.")
            return

        await update_schedule_message(
            interaction.guild_id,
            self.schedule_name,
            self.message_input.value.strip(),
        )

        await interaction.edit_original_response(
            content=f"Updated the message for **{self.schedule_name}**."
        )


class ScheduleListView(discord.ui.View):
    PAGE_SIZE = 5

    def __init__(
        self,
        owner_id: int,
        guild: discord.Guild,
        schedules: list[dict],
        timezone: str,
    ):
        super().__init__(timeout=300)

        self.owner_id = owner_id
        self.guild = guild
        self.schedules = schedules
        self.timezone = timezone
        self.current_page = 0

        self.total_pages = max(
            1,
            (len(schedules) + self.PAGE_SIZE - 1) // self.PAGE_SIZE,
        )

        self.update_buttons()

    def format_discord_time(self, time_str: str) -> str:
        try:
            hour, minute = map(int, time_str.split(":"))
            now = datetime.now(ZoneInfo(self.timezone))

            scheduled_time = now.replace(
                hour=hour,
                minute=minute,
                second=0,
                microsecond=0,
            )

            return f"<t:{int(scheduled_time.timestamp())}:t>"

        except (ValueError, TypeError, ZoneInfoNotFoundError):
            return f"`{time_str or 'Unknown'}`"

    def update_buttons(self):
        self.previous_button.disabled = self.current_page == 0
        self.next_button.disabled = self.current_page >= self.total_pages - 1

    def create_embed(self) -> discord.Embed:
        start = self.current_page * self.PAGE_SIZE
        page_schedules = self.schedules[start : start + self.PAGE_SIZE]

        embed = discord.Embed(
            title=f"{len(self.schedules)} Scheduled Messages",
            color=discord.Color.from_rgb(250, 192, 228),
        )

        for schedule in page_schedules:
            name = schedule.get("name") or "Unnamed schedule"
            channel_id = schedule.get("channel_id")
            channel = self.guild.get_channel(int(channel_id)) if channel_id else None

            channel_text = channel.mention if channel else f"`{channel_id or 'Unknown'}`"

            status = (
                "<:tickchecked:1556331542257598595>"
                if schedule.get("active", True)
                else "<:crosschecked:1556332674551914569>"
            )

            schedule_id = str(schedule.get("_id", "Unknown"))
            creator_id = schedule.get("created_by")
            time_str = str(schedule.get("time", "Unknown"))

            details = (
                f"**Time:** {self.format_discord_time(time_str)} "
                f"(`{time_str}` {self.timezone})\n"
                f"**Channel:** {channel_text}\n"
                f"**Status:** {status}\n"
                f"**ID:** `{schedule_id}`\n"
            )

            if creator_id:
                details += f"**Created by:** <@{creator_id}>\n"

            embed.add_field(
                name=name[:256],
                value=details[:1024],
                inline=False,
            )

        embed.set_footer(
            text=(
                f"{self.current_page + 1} of {self.total_pages} pages"
                " • Use /schedule edit, /schedule toggle, or "
                "/schedule delete to manage schedules."
            )
        )

        return embed

    async def interaction_check(
        self,
        interaction: discord.Interaction,
    ) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "Only the administrator who opened this list can use these buttons.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(
        label="Previous",
        style=discord.ButtonStyle.secondary,
    )
    async def previous_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        if self.current_page > 0:
            self.current_page -= 1

        self.update_buttons()

        await interaction.response.edit_message(
            embed=self.create_embed(),
            view=self,
        )

    @discord.ui.button(
        label="Next",
        style=discord.ButtonStyle.secondary,
    )
    async def next_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        if self.current_page < self.total_pages - 1:
            self.current_page += 1

        self.update_buttons()

        await interaction.response.edit_message(
            embed=self.create_embed(),
            view=self,
        )
