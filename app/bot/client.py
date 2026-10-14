import discord
from discord.ext import commands

from app.config import settings
from app.database.repositories.prefix_repository import (
    get_prefix as get_guild_prefix,
)
from app.services.schedule_service import ScheduleService


class Kitsu(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.guilds = True
        intents.members = True
        intents.message_content = True

        super().__init__(
            command_prefix=self.get_prefix,
            strip_after_prefix=True,
            intents=intents,
        )

        guild_id = getattr(settings, "discord_main_guild_id", None)

        if not guild_id:
            raise RuntimeError(
                "DISCORD_MAIN_GUILD_ID must be configured in settings."
            )

        self.main_guild_id = int(guild_id)
        self.schedule_service = None

    async def setup_hook(self):
        # Load extensions.
        await self.load_extension("app.cogs.emoji_gif")
        print("Emoji GIF extension loaded")

        await self.load_extension("app.cogs.prefix")
        print("Prefix extension loaded")

        await self.load_extension("app.cogs.schedules")
        print("Schedules extension loaded")

        await self.load_extension("app.cogs.sticky")
        print("Sticky extension loaded")

        await self.load_extension("app.cogs.steal_emoji")
        print("Steal emoji extension loaded")

        # Start the schedule service.
        self.schedule_service = ScheduleService(self)
        self.schedule_service.start()
        print("Schedule service started")

        guild = discord.Object(id=self.main_guild_id)

        # 1. Copy locally defined slash commands to the configured guild.
        self.tree.copy_global_to(guild=guild)

        # 2. Register slash commands in that guild.
        synced = await self.tree.sync(guild=guild)
        print(
            "Synced guild commands:",
            [command.name for command in synced],
        )

        # 3. Clear global command definitions locally.
        # The guild-specific copies have already been synced.
        self.tree.clear_commands(guild=None)

        # 4. Sync the empty global command list to Discord,
        # removing globally registered slash commands.
        await self.tree.sync()
        print("Global slash commands cleared.")

        print(
            "Local global definitions:",
            [command.name for command in self.tree.get_commands()],
        )

        print(
            "Guild slash commands:",
            [
                command.name
                for command in self.tree.get_commands(guild=guild)
            ],
        )

    async def get_prefix(self, message: discord.Message):
        # Ignore DMs.
        if message.guild is None:
            return ",k"

        # Allow prefix commands only in the configured server.
        if message.guild.id != self.main_guild_id:
            return "\0"

        return await get_guild_prefix(message.guild.id)

    async def on_message(self, message: discord.Message):
        # Prefix commands only work in servers, not DMs.
        if message.guild is None:
            return

        # Ignore messages from every other server.
        if message.guild.id != self.main_guild_id:
            return

        await self.process_commands(message)

    async def on_guild_join(self, guild: discord.Guild):
        # Leave any server other than the configured one.
        if guild.id != self.main_guild_id:
            print(
                f"Leaving unauthorized guild: "
                f"{guild.name} ({guild.id})"
            )
            await guild.leave()

    async def on_ready(self):
        print(f"Logged in as {self.user}")

        # Remove any unauthorized servers the bot was already in.
        for guild in list(self.guilds):
            if guild.id != self.main_guild_id:
                print(
                    f"Leaving unauthorized guild: "
                    f"{guild.name} ({guild.id})"
                )
                await guild.leave()

        guild = self.get_guild(self.main_guild_id)

        if guild:
            print(
                f"Connected to authorized server: "
                f"{guild.name} ({guild.id})"
            )
        else:
            print(
                f"WARNING: Not connected to configured guild "
                f"{self.main_guild_id}"
            )

        print(
            "Loaded prefix commands:",
            [command.name for command in self.commands],
        )

        # get_commands() without a guild returns global definitions.
        # This should be empty after clearing the global commands.
        print(
            "Local global slash commands:",
            [command.name for command in self.tree.get_commands()],
        )

        if guild:
            guild_ref = discord.Object(id=self.main_guild_id)
            print(
                "Local guild slash commands:",
                [
                    command.name
                    for command in self.tree.get_commands(guild=guild_ref)
                ],
            )