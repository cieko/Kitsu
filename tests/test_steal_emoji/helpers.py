import io
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
from PIL import Image

from app.bot.permissions import MODERATOR_ROLE_ID


def image_bytes(format="PNG", size=(320, 320), *, animated=False, duration=100):
    output = io.BytesIO()
    first = Image.new("RGBA", size, (255, 0, 0, 255))
    options = {}
    if animated:
        second = Image.new("RGBA", size, (0, 0, 255, 255))
        options = {"save_all": True, "append_images": [second], "duration": duration, "loop": 0}
    first.save(output, format=format, **options)
    return output.getvalue()


def sticker(format="png", item_id=123):
    return SimpleNamespace(
        id=item_id,
        name="wave",
        format=SimpleNamespace(name=format),
        url=f"https://cdn.discordapp.com/stickers/{item_id}.{'json' if format == 'lottie' else 'png'}",
        description="A friendly wave",
        emoji="👋",
    )


def message(*, stickers=(), snapshots=(), content=""):
    return SimpleNamespace(
        id=99,
        author=SimpleNamespace(id=8),
        channel=SimpleNamespace(id=3),
        stickers=list(stickers),
        message_snapshots=list(snapshots),
        content=content,
        embeds=[],
    )


def interaction(*, user_allowed=True, bot_allowed=True, guild=True):
    member = MagicMock(spec=discord.Member)
    member.id = 7
    member.guild_permissions = discord.Permissions.none()
    member.roles = [SimpleNamespace(id=MODERATOR_ROLE_ID)] if user_allowed else []
    server = SimpleNamespace(
        id=1,
        owner_id=99,
        name="Test Server",
        features=[],
        me=SimpleNamespace(guild_permissions=discord.Permissions(create_expressions=bot_allowed)),
        create_custom_emoji=AsyncMock(return_value=SimpleNamespace(id=1001, name="wave")),
        create_sticker=AsyncMock(return_value=SimpleNamespace(id=1002, name="wave")),
    )
    member.guild = server
    return SimpleNamespace(
        user=member,
        guild=server if guild else None,
        response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )
