import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from discord import app_commands


def member(user_id=2, **permissions):
    user = MagicMock(spec=discord.Member)
    user.id = user_id
    user.guild_permissions = discord.Permissions(**permissions)
    return user


def interaction(user, guild_id=1):
    return SimpleNamespace(
        user=user,
        guild=SimpleNamespace(id=guild_id) if guild_id else None,
        guild_id=guild_id,
        response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )


@pytest.mark.parametrize(
    "module_name,class_name,group_name,permission",
    [
        ("emoji_gif", "EmojiGif", "channel", "manage_channels"),
        ("translation", "TranslationCog", "translation", "manage_guild"),
    ],
)
@pytest.mark.parametrize("authorized", [False, True])
def test_every_configuration_subcommand_checks_member_permissions(
    import_app, module_name, class_name, group_name, permission, authorized
):
    module = import_app(f"app.cogs.{module_name}")

    async def check():
        client = discord.Client(intents=discord.Intents.none())
        try:
            cog = getattr(module, class_name)(client)
            group = getattr(cog, group_name)
            tree = app_commands.CommandTree(client)
            payload = group.to_dict(tree)
            assert (
                payload["default_member_permissions"]
                == discord.Permissions(**{permission: True}).value
            )
            assert payload["dm_permission"] is False
            for command in group.commands:
                current = interaction(member(**{permission: authorized}))
                assert await command._check_can_run(current) is authorized
                assert current.response.send_message.await_count == (0 if authorized else 1)
            if group_name == "translation":
                assert await cog.translate_command._check_can_run(interaction(member()))
        finally:
            await client.close()

    asyncio.run(check())


def test_permission_group_rejects_dm_and_accepts_administrator(import_app):
    permissions = import_app("app.bot.permissions")

    async def check():
        group = permissions.GuildPermissionGroup(
            name="test",
            description="Test",
            permission="manage_guild",
            permission_label="Manage Server",
        )
        assert not await group.interaction_check(interaction(member(), guild_id=None))
        assert await group.interaction_check(interaction(member(administrator=True)))

    asyncio.run(check())


@pytest.mark.parametrize("action", ["set", "reset"])
@pytest.mark.parametrize("authorized", [False, True])
def test_prefix_mutations_require_manage_server(import_app, monkeypatch, action, authorized):
    module = import_app("app.cogs.prefix")
    save = AsyncMock()
    reset = AsyncMock()
    monkeypatch.setattr(module, "set_prefix", save)
    monkeypatch.setattr(module, "reset_prefix", reset)
    ctx = SimpleNamespace(
        guild=SimpleNamespace(id=1), author=member(manage_guild=authorized), send=AsyncMock()
    )
    asyncio.run(module.Prefix.prefix.callback(module.Prefix(None), ctx, action, "!"))
    assert save.await_count == (1 if authorized and action == "set" else 0)
    assert reset.await_count == (1 if authorized and action == "reset" else 0)


def test_prefix_lookup_remains_public_and_whitespace_cannot_be_saved(import_app, monkeypatch):
    module = import_app("app.cogs.prefix")
    lookup, save = AsyncMock(return_value=",k"), AsyncMock()
    monkeypatch.setattr(module, "get_prefix", lookup)
    monkeypatch.setattr(module, "set_prefix", save)
    ctx = SimpleNamespace(guild=SimpleNamespace(id=1), author=member(), send=AsyncMock())
    cog = module.Prefix(None)
    asyncio.run(module.Prefix.prefix.callback(cog, ctx))
    lookup.assert_awaited_once_with(1)
    ctx.author = member(manage_guild=True)
    asyncio.run(module.Prefix.prefix.callback(cog, ctx, "set", "   "))
    save.assert_not_awaited()


@pytest.mark.parametrize(
    "user_permissions,bot_permissions,allowed",
    [
        ({}, {"create_expressions": True}, False),
        ({"manage_expressions": True}, {"create_expressions": True}, False),
        ({"create_expressions": True}, {}, False),
        ({"create_expressions": True}, {"create_expressions": True}, True),
        ({"administrator": True}, {"administrator": True}, True),
    ],
)
def test_public_emoji_button_checks_actor_and_bot(
    import_app, monkeypatch, user_permissions, bot_permissions, allowed
):
    module = import_app("app.cogs.steal_emoji")
    download = AsyncMock(return_value=b"image")
    monkeypatch.setattr(module.components, "download_file", download)

    async def check():
        current = interaction(member(**user_permissions))
        current.guild.name = "Test"
        current.guild.me = member(99, **bot_permissions)
        current.guild.create_custom_emoji = AsyncMock(return_value=SimpleNamespace(name="test"))
        item = module.StealItem(
            "emoji", "test", 123, "https://example.invalid/test.png", False, "png"
        )
        # The public preview's plain view dispatches directly to this button.
        await module.StealToServerButton(item).callback(current)
        assert current.guild.create_custom_emoji.await_count == int(allowed)
        assert download.await_count == int(allowed)

    asyncio.run(check())


@pytest.mark.parametrize(
    "user_id,guild_id,authorized,allowed",
    [(2, 1, False, False), (3, 1, True, False), (2, 9, True, False), (2, 1, True, True)],
)
def test_custom_setup_rechecks_owner_and_permissions_at_save(
    import_app, monkeypatch, user_id, guild_id, authorized, allowed
):
    module = import_app("app.services.custom_channel_ui")
    save = AsyncMock()
    monkeypatch.setattr(module.view, "set_channel_config", save)

    async def check():
        view = module.CustomChannelView(
            guild_id=1, channel=SimpleNamespace(id=5, mention="#test"), bot=None, owner_id=2
        )
        current = interaction(member(user_id, manage_channels=authorized), guild_id)
        current.response.edit_message = AsyncMock()
        await view._save_configuration(current)
        assert save.await_count == int(allowed)

    asyncio.run(check())
