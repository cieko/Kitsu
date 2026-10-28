import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from app.bot.permissions import MODERATOR_ROLE_ID

from .helpers import image_bytes, interaction


@pytest.mark.parametrize("kind", ["emoji", "sticker"])
@pytest.mark.parametrize(
    "actor,allowed",
    [
        ("member", False),
        ("expression_creator", False),
        ("expression_manager", False),
        ("message_moderator", False),
        ("other_role", False),
        ("moderator", True),
        ("administrator", True),
        ("owner", True),
        ("non_member", False),
    ],
)
def test_steal_only_uploads_for_admins_and_the_configured_moderator_role(
    import_app, monkeypatch, kind, actor, allowed
):
    module = import_app("app.cogs.steal_emoji")
    download = AsyncMock(return_value=image_bytes())
    monkeypatch.setattr(module.components, "download_file", download)

    async def check():
        current = interaction(user_allowed=False)
        permissions = {
            "expression_creator": "create_expressions",
            "expression_manager": "manage_expressions",
            "message_moderator": "manage_messages",
            "administrator": "administrator",
        }
        if actor in permissions:
            current.user.guild_permissions = discord.Permissions(**{permissions[actor]: True})
        elif actor in ("moderator", "other_role"):
            role_id = MODERATOR_ROLE_ID if actor == "moderator" else MODERATOR_ROLE_ID + 1
            current.user.roles = [SimpleNamespace(id=role_id)]
        elif actor == "owner":
            current.guild.owner_id = current.user.id
        elif actor == "non_member":
            current.user = MagicMock(spec=discord.User)
            current.user.id = 7
        item = module.StealItem(kind, "wave", 123, "https://example.com/asset", False, "png")

        await module.StealToServerButton(item).callback(current)

        assert download.await_count == int(allowed)
        assert current.response.defer.await_count == int(allowed)
        assert current.guild.create_custom_emoji.await_count == int(allowed and kind == "emoji")
        assert current.guild.create_sticker.await_count == int(allowed and kind == "sticker")
        if allowed:
            current.response.send_message.assert_not_awaited()
            assert "Successfully added" in current.followup.send.call_args.args[0]
        else:
            current.response.send_message.assert_awaited_once_with(
                "Only server admins and moderators can use the Steal button.",
                ephemeral=True,
            )
            current.followup.send.assert_not_awaited()

    asyncio.run(check())


@pytest.mark.parametrize("kind", ["emoji", "sticker"])
@pytest.mark.parametrize("authorized", [False, True])
def test_published_preview_rechecks_the_person_clicking_steal(
    import_app, monkeypatch, kind, authorized
):
    module = import_app("app.cogs.steal_emoji")
    download = AsyncMock(return_value=image_bytes())
    monkeypatch.setattr(module.components, "download_file", download)

    async def check():
        item = module.StealItem(kind, "wave", 123, "https://example.com/asset", False, "png")
        publisher = interaction()
        publisher.channel = SimpleNamespace(send=AsyncMock())
        preview = module.StealEmojiView([item], publisher.user.id, is_slash=True)
        await module.components.MakePublicButton(preview).callback(publisher)
        public_view = publisher.channel.send.call_args.kwargs["view"]
        button = next(
            child for child in public_view.walk_children()
            if isinstance(child, module.StealToServerButton)
        )
        current = interaction(user_allowed=authorized)
        current.user.id = 8
        current.user.guild_permissions = discord.Permissions(create_expressions=True)

        await button.callback(current)

        assert download.await_count == int(authorized)
        assert current.guild.create_custom_emoji.await_count == int(authorized and kind == "emoji")
        assert current.guild.create_sticker.await_count == int(authorized and kind == "sticker")
        if not authorized:
            current.response.send_message.assert_awaited_once()
            current.response.defer.assert_not_awaited()

    asyncio.run(check())


@pytest.mark.parametrize("kind", ["emoji", "sticker"])
def test_moderator_role_removal_blocks_an_existing_steal_button(import_app, monkeypatch, kind):
    module = import_app("app.cogs.steal_emoji")
    download = AsyncMock()
    monkeypatch.setattr(module.components, "download_file", download)

    async def check():
        current = interaction()
        item = module.StealItem(kind, "wave", 123, "https://example.com/asset", False, "png")
        button = module.StealToServerButton(item)
        current.user.roles = []

        await button.callback(current)

        download.assert_not_awaited()
        current.guild.create_custom_emoji.assert_not_awaited()
        current.guild.create_sticker.assert_not_awaited()
        current.response.defer.assert_not_awaited()
        current.response.send_message.assert_awaited_once()

    asyncio.run(check())
