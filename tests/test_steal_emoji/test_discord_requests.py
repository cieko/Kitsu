import asyncio
import base64
import io
from types import MethodType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from PIL import Image

from .helpers import image_bytes, interaction


@pytest.mark.parametrize(
    "kind,format,animated",
    [
        ("emoji", "png", False),
        ("emoji", "gif", True),
        ("sticker", "png", False),
        ("sticker", "apng", True),
        ("sticker", "gif", True),
    ],
)
def test_button_builds_a_real_discord_upload_request(
    import_app, monkeypatch, kind, format, animated
):
    """Run through discord.py's guild methods and HTTP payload serializers."""
    module = import_app("app.cogs.steal_emoji")
    data = image_bytes(
        "GIF" if format == "gif" else "PNG",
        (128, 128) if kind == "emoji" else (320, 320),
        animated=animated,
    )
    monkeypatch.setattr(module.components, "download_file", AsyncMock(return_value=data))

    async def check():
        captured = {}

        async def request(route, **kwargs):
            assert route.method == "POST"
            assert f"/guilds/1/{'emojis' if kind == 'emoji' else 'stickers'}" in route.url
            captured.update(kwargs)
            if kind == "emoji":
                payload = kwargs["json"]
                assert payload["name"] == "wave"
                assert payload["roles"] == []
                content_type, encoded = payload["image"].split(",", 1)
                assert content_type == (
                    "data:image/gif;base64" if animated else "data:image/png;base64"
                )
                assert base64.b64decode(encoded) == data
                return {"id": "1001", "name": "wave", "roles": [], "animated": animated}

            fields = {field["name"]: field for field in kwargs["form"]}
            captured["file"] = kwargs["files"][0]
            assert fields["name"]["value"] == "wave"
            assert fields["tags"]["value"] == "🍥"
            assert fields["file"]["content_type"] == (
                "image/gif" if format == "gif" else "image/png"
            )
            uploaded = fields["file"]["value"].read()
            assert uploaded == data
            with Image.open(io.BytesIO(uploaded)) as image:
                assert image.size == (320, 320)
                assert bool(getattr(image, "is_animated", False)) is animated
            return {
                "id": "1002",
                "name": "wave",
                "description": "",
                "guild_id": "1",
                "tags": "🍥",
                "format_type": {"png": 1, "apng": 2, "gif": 4}[format],
                "type": 2,
            }

        http = SimpleNamespace(request=AsyncMock(side_effect=request))
        http.create_custom_emoji = MethodType(discord.http.HTTPClient.create_custom_emoji, http)
        http.create_guild_sticker = MethodType(discord.http.HTTPClient.create_guild_sticker, http)
        current = interaction()
        guild = MagicMock(spec=discord.Guild)
        guild.id, guild.name, guild.features, guild.me = 1, "Test Server", [], current.guild.me
        guild._state = SimpleNamespace(http=http, cache_guild_expressions=False)
        guild.create_custom_emoji = MethodType(discord.Guild.create_custom_emoji, guild)
        guild.create_sticker = MethodType(discord.Guild.create_sticker, guild)
        current.guild = guild
        item = module.StealItem(kind, "wave", 123, "https://example.com/asset", animated, format)
        await module.StealToServerButton(item).callback(current)
        http.request.assert_awaited_once()
        current.followup.send.assert_awaited_once()
        assert "Successfully added" in current.followup.send.call_args.args[0]
        if kind == "sticker":
            assert captured["file"].fp.closed

    asyncio.run(check())
