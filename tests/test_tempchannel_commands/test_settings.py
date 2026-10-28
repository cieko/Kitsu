from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest
from discord.ext import commands

from .helpers import make_channel, make_guild, make_member


@pytest.mark.parametrize("operation", ["add", "remove"])
def test_membership_changes_keep_commands_disabled(import_app, monkeypatch, operation):
    module = import_app("app.services.ghosty_tempchannel_service")

    async def check():
        guild = make_guild()
        owner, target = make_member(guild, 100), make_member(guild, 101, [2])
        channel = make_channel(
            guild,
            {
                guild.get_role(2): discord.PermissionOverwrite(use_application_commands=True),
            },
        )
        service = module.GhostyTempChannelService(None)
        service._get_owned_channel = AsyncMock(return_value={"creator_id": "100"})
        service._get_staff_roles = AsyncMock(return_value=[])
        service.get_settings = AsyncMock(return_value={"max_ordinary_members": 20})
        monkeypatch.setattr(
            module.repository,
            "get_active_channel",
            AsyncMock(return_value={"creator_id": "100", "member_ids": ["101"]}),
        )
        monkeypatch.setattr(
            module.repository, "add_member", AsyncMock(return_value=(True, "added"))
        )
        monkeypatch.setattr(module.repository, "remove_member", AsyncMock(return_value=True))
        with (
            patch.object(discord.TextChannel, "edit", new=AsyncMock()) as edit,
            patch.object(
                discord.TextChannel, "set_permissions", new=AsyncMock()
            ) as set_permissions,
        ):
            await getattr(service, f"{operation}_member")(owner, channel, target)
            backend = edit.call_args.kwargs["overwrites"]
            if operation == "add":
                flags = set_permissions.call_args.kwargs
                assert flags["use_application_commands"] is False
                backend[target] = discord.PermissionOverwrite(
                    **{name: value for name, value in flags.items() if name != "reason"}
                )
            else:
                assert set_permissions.call_args.kwargs["overwrite"] is None
                backend.pop(target, None)
            assert not make_channel(guild, backend).permissions_for(target).use_application_commands

    asyncio.run(check())


@pytest.mark.parametrize("is_owner", [True, False])
@pytest.mark.parametrize("thumbnail_source", ["server", "file", "none"])
def test_text_trigger_is_available_without_slash_or_manager_permissions(
    import_app,
    monkeypatch,
    tmp_path,
    is_owner,
    thumbnail_source,
):
    module = import_app("app.cogs.ghosty")
    thumbnail_path = tmp_path / "tempchannel-settings.webp"
    if thumbnail_source == "file":
        thumbnail_path.write_bytes(b"test-thumbnail")
    monkeypatch.setattr(module.prefix_commands, "TEMPCHANNEL_SETTINGS_THUMBNAIL", thumbnail_path)

    async def check():
        guild = make_guild()
        guild.name = "Example **Server**"
        guild.icon = (
            SimpleNamespace(url="https://cdn.discordapp.com/icons/1/server.png")
            if thumbnail_source != "none"
            else None
        )
        member = make_member(guild, 100 if is_owner else 101)
        guild.get_member = lambda member_id: member if member_id == member.id else None
        channel = make_channel(
            guild, {guild.default_role: discord.PermissionOverwrite(use_application_commands=False)}
        )
        assert not channel.permissions_for(member).use_application_commands
        intents = discord.Intents.default()
        intents.message_content = True
        bot = commands.Bot(command_prefix="!", strip_after_prefix=True, intents=intents)
        try:
            await bot._async_setup_hook()
            bot._connection.user = SimpleNamespace(id=900)
            cog = module.GhostyCog(bot)
            cog.cog_load = AsyncMock()
            cog.tempchannel_service._get_owned_channel = AsyncMock(
                return_value={"creator_id": "100"},
                side_effect=None
                if is_owner
                else module.TempChannelError("Only the channel creator can perform this action."),
            )
            cog.tempchannel_service.ensure_command_permissions = AsyncMock()
            await bot.add_cog(cog)
            message = SimpleNamespace(
                content="! ghosty tempchannel settings",
                author=member,
                guild=guild,
                channel=channel,
                _state=MagicMock(),
                attachments=[],
            )
            with patch.object(commands.Context, "send", new=AsyncMock()) as send:
                await bot.process_commands(message)
                send.assert_awaited_once()
                if is_owner:
                    view = send.call_args.kwargs["view"]
                    assert isinstance(view, discord.ui.LayoutView)
                    assert "embed" not in send.call_args.kwargs
                    # Validate the actual outgoing V2 tree: dropdowns inside
                    # the coloured card, deletion in a separate action row.
                    from discord.http import handle_message_parameters

                    with handle_message_parameters(view=view) as params:
                        assert params.payload["flags"] & (1 << 15)
                        card, outside_row = params.payload["components"]
                    assert card["type"] == 17
                    assert card["accent_color"] == 0x7CC6B0
                    selects = [
                        row["components"][0] for row in card["components"] if row["type"] == 1
                    ]
                    assert [select["type"] for select in selects] == [3, 3, 5, 5]
                    assert outside_row["type"] == 1
                    assert outside_row["components"][0]["label"] == "Delete channel"
                    assert outside_row["components"][0]["style"] == 4
                    header = card["components"][0]
                    if thumbnail_source == "none":
                        assert header["type"] == 10
                        title = header["content"]
                    else:
                        assert header["type"] == 9
                        title = header["components"][0]["content"]
                        assert header["accessory"]["type"] == 11
                        assert header["accessory"]["media"]["url"] == (
                            "attachment://tempchannel-settings.webp"
                            if thumbnail_source == "file"
                            else guild.icon.url
                        )
                    assert title.startswith("### Example \\*\\*Server\\*\\*\n")
                    if thumbnail_source == "file":
                        assert send.call_args.kwargs["file"].filename == "tempchannel-settings.webp"
                    else:
                        assert "file" not in send.call_args.kwargs
                    assert view.owner_id == member.id
                    assert view.channel_id == channel.id
                    assert view.visibility_select.view is view
                    assert view.duration_select.view is view
                    assert view.delete_channel_button.view is view
                    cog.tempchannel_service.ensure_command_permissions.assert_awaited_once_with(
                        channel
                    )
                    # Buttons use the owner check, not application-command permissions.
                    current = SimpleNamespace(
                        user=member,
                        guild=guild,
                        channel=channel,
                        channel_id=channel.id,
                        response=SimpleNamespace(send_message=AsyncMock()),
                    )
                    assert await view.interaction_check(current)
                    cog.tempchannel_service.set_visibility = AsyncMock()
                    view.visibility_select._values = ["private"]
                    await view.visibility_select.callback(current)
                    cog.tempchannel_service.set_visibility.assert_awaited_once_with(
                        member, channel, "private"
                    )
                else:
                    assert "view" not in send.call_args.kwargs
                    cog.tempchannel_service.ensure_command_permissions.assert_not_awaited()
        finally:
            await bot.close()

    asyncio.run(check())
