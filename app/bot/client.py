import discord
from discord.ext import commands

from app.config import settings


class Kitsu(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.guilds = True
        intents.members = True
        intents.message_content = True

        super().__init__(
            command_prefix="!",
            intents=intents,
        )

    async def setup_hook(self):
        await self.load_extension("app.cogs.emoji_gif")

        guild = discord.Object(
            id=int(settings.discord_main_guild_id)
        )

        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)

        await self.tree.sync()

        print("Slash commands synced globally and to main guild.")

    async def on_ready(self):
        print(f"Logged in as {self.user}")
        print(f"Connected to {len(self.guilds)} server(s)")