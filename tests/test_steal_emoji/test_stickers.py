import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from .helpers import message, sticker


@pytest.mark.parametrize("format", ["png", "apng", "gif", "lottie"])
def test_native_and_forwarded_stickers_are_extracted_without_duplicates(import_app, format):
    module = import_app("app.cogs.steal_emoji")
    original = sticker(format)
    forwarded = SimpleNamespace(content="", embeds=[], stickers=[original, sticker(format, 456)])
    source = message(stickers=[original], snapshots=[forwarded])
    items = module.get_message_items(source)
    assert [item.item_id for item in items] == [123, 456]
    assert all(item.item_type == "sticker" and item.source_format == format for item in items)
    assert items[0].animated is (format != "png")
    assert items[0].description == "A friendly wave" and items[0].emoji == "👋"
    assert items[0].export_options == (
        ["JSON"]
        if format == "lottie"
        else ["GIF", "APNG"]
        if format in {"gif", "apng"}
        else ["PNG", "GIF", "WEBP", "JPEG"]
    )


def test_partial_sticker_items_get_valid_upload_metadata(import_app):
    module = import_app("app.cogs.steal_emoji")
    partial = discord.StickerItem(state=None, data={"id": "123", "name": "wave", "format_type": 2})
    item = module.get_message_stickers(message(stickers=[partial]))[0]
    assert item.source_format == "apng" and item.url.endswith("123.png")
    assert item.description == "" and item.emoji == "🍥"


@pytest.mark.parametrize("slash", [False, True])
@pytest.mark.parametrize("format", ["png", "apng", "gif", "lottie"])
def test_sticker_previews_keep_the_existing_card_and_controls(import_app, slash, format):
    module = import_app("app.cogs.steal_emoji")

    async def check():
        items = module.get_message_stickers(message(stickers=[sticker(format)]))
        view = module.StealEmojiView(items, owner_id=7, is_slash=slash)
        assert isinstance(view, discord.ui.LayoutView) and view.has_components_v2()
        assert len(view.children) == (2 if slash else 1)
        container = view.children[0]
        assert isinstance(container.children[0], discord.ui.Section)
        assert isinstance(container.children[0].accessory, module.StealToServerButton)
        assert container.children[0].accessory.label == "Steal"
        assert "Enlarged Sticker" in container.children[0].children[0].content
        assert isinstance(
            container.children[1],
            discord.ui.TextDisplay if format == "lottie" else discord.ui.MediaGallery,
        )
        select = next(
            child for child in view.walk_children() if isinstance(child, module.StealExportSelect)
        )
        assert [option.value for option in select.options] == items[0].export_options
        assert (
            any(isinstance(child, module.MakePublicButton) for child in view.walk_children())
            is slash
        )
        await view.expire_view()
        assert all(
            child.disabled
            for child in view.walk_children()
            if isinstance(child, (discord.ui.Button, discord.ui.Select))
        )

    asyncio.run(check())


def test_prefix_command_previews_a_replied_sticker(import_app):
    module = import_app("app.cogs.steal_emoji")

    async def check():
        target = message(stickers=[sticker()])
        ctx = SimpleNamespace(
            author=SimpleNamespace(id=7),
            message=SimpleNamespace(
                id=100, reference=SimpleNamespace(resolved=None, message_id=99)
            ),
            channel=SimpleNamespace(fetch_message=AsyncMock(return_value=target)),
            reply=AsyncMock(return_value=SimpleNamespace(edit=AsyncMock())),
        )
        cog = module.StealEmoji(None)
        await cog.steal_prefix.callback(cog, ctx)
        view = ctx.reply.call_args.kwargs["view"]
        try:
            assert view.items[0].item_type == "sticker" and not view.is_slash
            assert "embed" not in ctx.reply.call_args.kwargs
        finally:
            view.expiry_task.cancel()
            await asyncio.gather(view.expiry_task, return_exceptions=True)

    asyncio.run(check())
