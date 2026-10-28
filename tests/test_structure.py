import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import discord
from discord import app_commands
from discord.ext import commands


def test_application_and_test_modules_stay_within_400_lines():
    root = Path(__file__).resolve().parents[1]
    oversized = []
    for directory in (root / "app", root / "tests"):
        for path in directory.rglob("*.py"):
            count = len(path.read_text(encoding="utf-8-sig").splitlines())
            if count > 400:
                oversized.append(f"{path.relative_to(root)}: {count}")
    assert not oversized, "Split oversized modules by responsibility:\n" + "\n".join(oversized)


def test_reorganized_extensions_register_commands_with_their_cogs(import_app, monkeypatch):
    extensions = {
        "emoji_gif": "EmojiGif",
        "emoji_mood": "EmojiMood",
        "ghosty": "GhostyCog",
        "schedules": "Schedules",
        "steal_emoji": "StealEmoji",
        "sticky": "Sticky",
    }

    async def check():
        bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
        bot.main_guild_id = 1
        try:
            for name, class_name in extensions.items():
                module = import_app(f"app.cogs.{name}")
                monkeypatch.setattr(getattr(module, class_name), "cog_load", AsyncMock())
                await bot.load_extension(f"app.cogs.{name}")
            for cog in bot.cogs.values():
                for command in cog.walk_app_commands():
                    if isinstance(command, app_commands.Command):
                        assert command.binding is cog, command.qualified_name
            ghosty = bot.tree.get_command("ghosty")
            assert ghosty.get_command("tempchannel").get_command("visibility")
            assert ghosty.get_command("tempchannels").get_command("role-set")
            assert bot.get_command("ghosty tempchannel settings")
            assert bot.tree.get_command("translation") is None
            assert bot.tree.get_command("translate") is None
            assert {command.name for command in bot.tree.get_command("sticky").commands} == {
                "add",
                "edit",
                "remove",
                "list",
                "toggle",
            }
        finally:
            await bot.close()

    asyncio.run(check())
