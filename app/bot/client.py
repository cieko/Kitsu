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
        
        self.schedule_service = None

    async def setup_hook(self):
        await self.load_extension("app.cogs.emoji_gif")
        print("Emoji GIF extension loaded")

        await self.load_extension("app.cogs.prefix")
        print("Prefix extension loaded")

        await self.load_extension("app.cogs.schedules")
        print("Schedules extension loaded")

        self.schedule_service = ScheduleService(self)
        self.schedule_service.start()
        print("Schedule service started")

        print(f"Loaded commands: {[command.name for command in self.commands]}")

        # guild = discord.Object(
        #     id=int(settings.discord_main_guild_id)
        # )

        # self.tree.copy_global_to(guild=guild)
        # await self.tree.sync(guild=guild)

        await self.tree.sync()

        # print("Slash commands synced globally and to main guild.")
        print("Slash commands synced globally.")

    async def get_prefix(self, message):
        if not message.guild:
            # return commands.when_mentioned_or(",k")(self, message)
            return ",k"

        prefix = await get_guild_prefix(message.guild.id)

        # print(f"PREFIX: {prefix!r}")
        # print(f"PREFIX LENGTH: {len(prefix)}")

        return prefix

    async def on_message(self, message):
        # print(f"Message received: {message.content!r}")

        ctx = await self.get_context(message)

        # print(f"Prefix: {ctx.prefix!r}")
        # print(f"View buffer: {ctx.view.buffer!r}")
        # print(f"View index: {ctx.view.index}")
        # print(f"Invoked with: {ctx.invoked_with!r}")
        # print(f"Command: {ctx.command}")

        command = self.get_command("prefix")
        # print(f"Registered command: {command}")
        # print(f"Command aliases: {command.aliases if command else None}")

        await self.process_commands(message)

    async def on_ready(self):
        print(f"Logged in as {self.user}")
        print(f"Connected to {len(self.guilds)} server(s)")
        print(f"Loaded commands: {[command.name for command in self.commands]}")