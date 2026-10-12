
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import discord
from discord import app_commands
from discord.ext import commands

from app.database.repositories.schedule_repository import (
    add_schedule,
    delete_schedule,
    delete_all_schedules,
    get_schedule,
    get_schedules,
    get_timezone,
    set_schedule_active,
    set_timezone,
    update_schedule_message,
)
from app.services.schedule_service import parse_message_script


# --------------------------------------------------
# AUTOCOMPLETE
# --------------------------------------------------

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
            choices.append(
                app_commands.Choice(name="all", value="all")
            )

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


# --------------------------------------------------
# CONFIRM DELETE ALL
# --------------------------------------------------

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


# --------------------------------------------------
# EDIT SCHEDULE MODAL
# --------------------------------------------------

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
            placeholder=(
                "{embed}\n"
                "{color: #FAC0E4}\n"
                "{description: Your message here}"
            ),
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
            await interaction.edit_original_response(
                content="Schedule not found."
            )
            return

        await update_schedule_message(
            interaction.guild_id,
            self.schedule_name,
            self.message_input.value.strip(),
        )

        await interaction.edit_original_response(
            content=f"Updated the message for **{self.schedule_name}**."
        )


# --------------------------------------------------
# PAGINATED SCHEDULE LIST
# --------------------------------------------------

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
        self.next_button.disabled = (
            self.current_page >= self.total_pages - 1
        )

    def create_embed(self) -> discord.Embed:
        start = self.current_page * self.PAGE_SIZE
        page_schedules = self.schedules[start:start + self.PAGE_SIZE]

        embed = discord.Embed(
            title=f"{len(self.schedules)} Scheduled Messages",
            color=discord.Color.from_rgb(250, 192, 228),
        )

        for schedule in page_schedules:
            name = schedule.get("name") or "Unnamed schedule"
            channel_id = schedule.get("channel_id")
            channel = (
                self.guild.get_channel(int(channel_id))
                if channel_id
                else None
            )

            channel_text = (
                channel.mention
                if channel
                else f"`{channel_id or 'Unknown'}`"
            )

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


# --------------------------------------------------
# SCHEDULE COMMAND GROUP
# --------------------------------------------------

class Schedules(commands.GroupCog, group_name="schedule"):
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

    # --------------------------------------------------
    # TIMEZONE
    # --------------------------------------------------

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
            content=(
                f"Requested timezone: `{timezone}`\n"
                f"Saved timezone: `{saved_timezone}`"
            )
        )

    # --------------------------------------------------
    # ADD SCHEDULE
    # --------------------------------------------------

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
            await interaction.edit_original_response(
                content="Please provide a schedule name."
            )
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
            str(item.get("name", "")).casefold() == name.casefold()
            for item in existing_schedules
        ):
            await interaction.edit_original_response(
                content=f"A schedule named **{name}** already exists."
            )
            return

        if not message:
            await interaction.edit_original_response(
                content="The message script cannot be empty."
            )
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

    # --------------------------------------------------
    # LIST SCHEDULES
    # --------------------------------------------------

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

    # --------------------------------------------------
    # DELETE SCHEDULE
    # --------------------------------------------------

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

    # --------------------------------------------------
    # EDIT SCHEDULE
    # --------------------------------------------------

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

    # --------------------------------------------------
    # TOGGLE SCHEDULE
    # --------------------------------------------------

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

        await interaction.edit_original_response(
            content=f"Schedule **{name}** has been {status}."
        )


# --------------------------------------------------
# EXTENSION SETUP
# --------------------------------------------------

async def setup(bot: commands.Bot):
    await bot.add_cog(Schedules(bot))