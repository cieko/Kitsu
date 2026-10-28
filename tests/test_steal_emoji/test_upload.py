import asyncio
import io
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest
from PIL import Image

from .helpers import image_bytes, interaction


@pytest.mark.parametrize("format,animated", [("png", False), ("gif", True), ("apng", True)])
def test_steal_uploads_stickers_with_the_correct_api_and_file(
    import_app, monkeypatch, format, animated
):
    module = import_app("app.cogs.steal_emoji")
    data = image_bytes("GIF" if format == "gif" else "PNG", animated=animated)
    download = AsyncMock(return_value=data)
    monkeypatch.setattr(module.components, "download_file", download)

    async def check():
        current = interaction()
        item = module.StealItem(
            "sticker",
            "wave",
            123,
            "https://example.com/sticker",
            animated,
            format,
            description="A friendly wave",
            emoji="👋",
        )
        captured = {}

        async def create(**kwargs):
            captured.update(kwargs)
            captured["bytes"] = kwargs["file"].fp.read()
            return SimpleNamespace(id=1002, name=kwargs["name"])

        current.guild.create_sticker.side_effect = create
        await module.StealToServerButton(item).callback(current)
        current.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
        current.guild.create_sticker.assert_awaited_once()
        current.guild.create_custom_emoji.assert_not_awaited()
        download.assert_awaited_once_with(item.url)
        assert captured["name"] == "wave" and captured["description"] == "A friendly wave"
        assert captured["emoji"] == "👋"
        assert captured["bytes"] == data
        assert captured["file"].filename.endswith(".gif" if format == "gif" else ".png")
        assert captured["file"].fp.closed
        current.followup.send.assert_awaited_once()
        assert "Successfully added sticker" in current.followup.send.call_args.args[0]

    asyncio.run(check())


@pytest.mark.parametrize("animated", [False, True])
def test_steal_uploads_valid_emoji_bytes_to_the_server(import_app, monkeypatch, animated):
    module = import_app("app.cogs.steal_emoji")
    data = image_bytes("GIF" if animated else "PNG", (128, 128), animated=animated)
    monkeypatch.setattr(module.components, "download_file", AsyncMock(return_value=data))

    async def check():
        current = interaction()
        item = module.StealItem(
            "emoji",
            "wave",
            123,
            "https://example.com/emoji",
            animated,
            "gif" if animated else "png",
        )
        await module.StealToServerButton(item).callback(current)
        current.guild.create_custom_emoji.assert_awaited_once_with(
            name="wave",
            image=data,
            reason=f"Emoji added by {current.user}",
        )
        current.guild.create_sticker.assert_not_awaited()
        assert "Successfully added" in current.followup.send.call_args.args[0]

    asyncio.run(check())


@pytest.mark.parametrize(
    "user_allowed,bot_allowed,guild",
    [(False, True, True), (True, False, True), (True, True, False)],
)
@pytest.mark.parametrize("kind", ["emoji", "sticker"])
def test_upload_permissions_are_required_before_downloading(
    import_app, monkeypatch, user_allowed, bot_allowed, guild, kind
):
    module = import_app("app.cogs.steal_emoji")
    download = AsyncMock()
    monkeypatch.setattr(module.components, "download_file", download)
    current = interaction(user_allowed=user_allowed, bot_allowed=bot_allowed, guild=guild)
    item = module.StealItem(kind, "wave", 123, "https://example.com/asset", False, "png")
    asyncio.run(module.StealToServerButton(item).callback(current))
    download.assert_not_awaited()
    current.response.defer.assert_not_awaited()
    current.response.send_message.assert_awaited_once()


@pytest.mark.parametrize("kind", ["emoji", "sticker"])
@pytest.mark.parametrize("failure", ["download", "discord"])
def test_failed_uploads_report_failure_without_claiming_success(
    import_app, monkeypatch, kind, failure
):
    module = import_app("app.cogs.steal_emoji")
    download = AsyncMock(return_value=image_bytes())
    if failure == "download":
        download.side_effect = RuntimeError("CDN unavailable")
    monkeypatch.setattr(module.components, "download_file", download)
    current = interaction()
    target = (
        current.guild.create_sticker if kind == "sticker" else current.guild.create_custom_emoji
    )
    if failure == "discord":
        target.side_effect = discord.HTTPException(
            SimpleNamespace(status=400, reason="Bad Request"),
            "No expression slots available",
        )
    item = module.StealItem(kind, "wave", 123, "https://example.com/asset", False, "png")
    asyncio.run(module.StealToServerButton(item).callback(current))
    current.followup.send.assert_awaited_once()
    assert "Successfully" not in current.followup.send.call_args.args[0]
    assert kind in current.followup.send.call_args.args[0]
    assert target.await_count == int(failure == "discord")


@pytest.mark.parametrize(
    "features,allowed", [([], False), (["VERIFIED"], True), (["PARTNERED"], True)]
)
def test_lottie_uploads_respect_discord_guild_restrictions(
    import_app, monkeypatch, features, allowed
):
    module = import_app("app.cogs.steal_emoji")
    payload = b'{"v":"5.5.7","w":320,"h":320,"layers":[]}'
    monkeypatch.setattr(module.components, "download_file", AsyncMock(return_value=payload))

    async def check():
        current = interaction()
        current.guild.features = features
        item = module.StealItem(
            "sticker", "wave", 123, "https://example.com/wave.json", True, "lottie"
        )
        captured = {}

        async def create(**kwargs):
            captured["filename"] = kwargs["file"].filename
            captured["bytes"] = kwargs["file"].fp.read()
            return SimpleNamespace(name="wave")

        current.guild.create_sticker.side_effect = create
        await module.StealToServerButton(item).callback(current)
        assert current.guild.create_sticker.await_count == int(allowed)
        if allowed:
            assert captured == {"filename": "sticker_123.json", "bytes": payload}
        else:
            assert "verified or partnered" in current.followup.send.call_args.args[0]

    asyncio.run(check())


@pytest.mark.parametrize("sticker,size", [(False, (512, 256)), (True, (640, 320))])
@pytest.mark.parametrize("animated", [False, True])
def test_server_uploads_resize_images_and_preserve_animation(import_app, sticker, size, animated):
    module = import_app("app.cogs.steal_emoji.upload")
    source = image_bytes("PNG", size, animated=animated)
    data, extension = module.prepare_image(source, sticker=sticker)
    with Image.open(io.BytesIO(data)) as image:
        assert image.size == ((320, 320) if sticker else (128, 128))
        assert image.n_frames == (2 if animated else 1)
        assert bool(getattr(image, "is_animated", False)) is animated
        if animated:
            assert image.info["duration"] == 100
        assert image.convert("RGBA").getpixel((0, 0))[3] == 0
    assert extension == ("gif" if animated and not sticker else "png")
    assert len(data) <= (module.STICKER_UPLOAD_LIMIT if sticker else module.EMOJI_UPLOAD_LIMIT)


def test_oversized_and_overlong_stickers_are_rejected_before_the_api(import_app, monkeypatch):
    module = import_app("app.cogs.steal_emoji.upload")
    with pytest.raises(ValueError, match="5 seconds"):
        module.prepare_image(image_bytes(animated=True, duration=3000), sticker=True)
    monkeypatch.setattr(module, "STICKER_UPLOAD_LIMIT", 10)
    with pytest.raises(ValueError, match="larger"):
        module.prepare_image(image_bytes(), sticker=True)
    with pytest.raises(ValueError, match="unreadable"):
        module.prepare_image(b"not an image", sticker=False)
