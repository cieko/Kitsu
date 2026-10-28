from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from .helpers import make_channel, make_guild, make_member, prepare_creation


@pytest.mark.parametrize("uncached_role", [False, True])
def test_creation_inherits_category_then_disables_commands(import_app, monkeypatch, uncached_role):
    module = import_app("app.services.ghosty_tempchannel_service")

    async def check():
        service, guild, owner, category = prepare_creation(module, monkeypatch)
        ordinary, staff = make_member(guild, 101, [2]), make_member(guild, 102, [3])
        inherited = discord.PermissionOverwrite(
            view_channel=True, attach_files=False, use_application_commands=True
        )
        category.overwrites = {
            discord.Object(id=2, type=discord.Role)
            if uncached_role
            else guild.get_role(2): inherited,
            owner: discord.PermissionOverwrite(use_application_commands=True),
        }
        if uncached_role:
            get_role = guild.get_role
            guild.get_role = lambda role_id: None if role_id == 2 else get_role(role_id)
        http_create = AsyncMock()

        def edit_channel(channel_id, **kwargs):
            return {
                "id": str(channel_id),
                "type": 0,
                "name": "temporary",
                "position": 0,
                "permission_overwrites": kwargs["permission_overwrites"],
            }

        http_edit = AsyncMock(side_effect=edit_channel)
        guild._state = SimpleNamespace(
            http=SimpleNamespace(
                create_channel=http_create,
                edit_channel=http_edit,
            )
        )

        async def create_channel(**kwargs):
            # Exercise the installed library's real overwrite serialization.
            await discord.Guild._create_channel(
                guild, channel_type=discord.ChannelType.text, **kwargs
            )
            assert "overwrites" not in kwargs
            return make_channel(guild, category.overwrites, state=guild._state)

        guild.create_text_channel = AsyncMock(side_effect=create_channel)

        channel = await service.create_channel(owner, "temporary", "1h")
        for member in (owner, ordinary, staff):
            permissions = channel.permissions_for(member)
            assert permissions.view_channel
            assert permissions.send_messages
            assert not permissions.use_application_commands
        assert channel.permissions_for(staff).manage_channels
        assert not channel.permissions_for(ordinary).attach_files
        # Copying permissions must not change the category's allow flags.
        assert inherited.use_application_commands is True
        assert category.overwrites[owner].use_application_commands is True
        # Inherit on creation instead of asking Discord to re-grant every
        # category permission using the bot's guild-level permission bits.
        assert http_create.call_args.kwargs["permission_overwrites"] == []
        http_edit.assert_awaited_once()
        role_payload = next(
            item for item in http_edit.call_args.kwargs["permission_overwrites"] if item["id"] == 2
        )
        assert role_payload["type"] == 0
        module.repository.create_channel_record.assert_awaited_once()

    asyncio.run(check())


@pytest.mark.parametrize(
    "outcome",
    [
        "deleted",
        "cancelled",
        "non_owner",
        "non_owner_confirm",
        "non_owner_cancel",
        "wrong_channel",
        "owner_changed",
        "forbidden",
        "already_deleted",
    ],
)
def test_delete_button_confirmation_flow(import_app, monkeypatch, outcome):
    module = import_app("app.cogs.ghosty")
    service_module = import_app("app.services.ghosty_tempchannel_service")

    async def check():
        guild = make_guild()
        guild.name = "Example Server"
        owner, other = make_member(guild, 100), make_member(guild, 101)
        events = []

        async def delete_channel(*args, **kwargs):
            assert events == ["acknowledged"]
            events.append("deleted")
            if outcome in {"forbidden", "already_deleted"}:
                error = discord.Forbidden if outcome == "forbidden" else discord.NotFound
                raise error(
                    SimpleNamespace(status=403 if outcome == "forbidden" else 404, reason=outcome),
                    {"message": outcome, "code": 50013 if outcome == "forbidden" else 10003},
                )

        http_delete = AsyncMock(side_effect=delete_channel)
        channel = make_channel(
            guild,
            {},
            state=SimpleNamespace(
                http=SimpleNamespace(delete_channel=http_delete),
            ),
        )
        guild.get_channel = lambda channel_id: channel if channel_id == channel.id else None
        record = {"creator_id": "100", "channel_id": "50", "name": "temporary"}
        get_record = AsyncMock(return_value=record)
        mark_deleted = AsyncMock(return_value=True)
        mark_notified = AsyncMock(return_value=True)
        monkeypatch.setattr(service_module.repository, "get_active_channel", get_record)
        monkeypatch.setattr(service_module.repository, "mark_channel_deleted", mark_deleted)
        monkeypatch.setattr(service_module.repository, "mark_deletion_notified", mark_notified)
        owner.send = AsyncMock()
        service = service_module.GhostyTempChannelService(None)
        view = module.GhostyTempChannelSettingsView(
            cog=SimpleNamespace(tempchannel_service=service),
            owner_id=owner.id,
            channel_id=channel.id,
            server_name=guild.name,
        )
        view.on_error = AsyncMock()

        def interaction(user):
            return SimpleNamespace(
                user=user,
                guild=guild,
                channel=channel,
                channel_id=channel.id,
                data={},
                response=SimpleNamespace(
                    send_message=AsyncMock(),
                    edit_message=AsyncMock(),
                    defer=AsyncMock(side_effect=lambda **kwargs: events.append("acknowledged")),
                ),
                followup=SimpleNamespace(send=AsyncMock()),
            )

        first = interaction(other if outcome == "non_owner" else owner)
        # Use the library's dispatcher so nested action-row and view checks run.
        await view._scheduled_task(view.delete_channel_button, first)
        view.on_error.assert_not_awaited()
        first.response.send_message.assert_awaited_once()
        if outcome == "non_owner":
            assert "Only the owner" in first.response.send_message.call_args.args[0]
            assert "view" not in first.response.send_message.call_args.kwargs
            get_record.assert_not_awaited()
            http_delete.assert_not_awaited()
            return

        assert first.response.send_message.call_args.kwargs["ephemeral"]
        confirmation = first.response.send_message.call_args.kwargs["view"]
        confirmation.on_error = AsyncMock()
        confirm_button, cancel_button = confirmation.children
        http_delete.assert_not_awaited()
        final = interaction(other if outcome.startswith("non_owner_") else owner)
        if outcome == "wrong_channel":
            final.channel_id = 999
        elif outcome == "owner_changed":
            get_record.return_value = {**record, "creator_id": "101"}

        selected = cancel_button if outcome in {"cancelled", "non_owner_cancel"} else confirm_button
        await confirmation._scheduled_task(selected, final)
        confirmation.on_error.assert_not_awaited()
        if outcome == "cancelled":
            final.response.edit_message.assert_awaited_once_with(
                content="Channel deletion cancelled.",
                view=None,
            )
        elif outcome in {"non_owner_confirm", "non_owner_cancel", "wrong_channel"}:
            final.response.send_message.assert_awaited_once()
            assert final.response.send_message.call_args.kwargs["ephemeral"]
            final.response.defer.assert_not_awaited()
        elif outcome in {"owner_changed", "forbidden"}:
            final.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
            final.followup.send.assert_awaited_once()
            assert final.followup.send.call_args.kwargs["ephemeral"]
            assert (
                "Only the channel creator"
                if outcome == "owner_changed"
                else "Discord could not delete"
            ) in final.followup.send.call_args.args[0]
        else:
            final.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
            http_delete.assert_awaited_once_with(
                channel.id, reason="Ghosty temporary-channel cleanup"
            )
            mark_deleted.assert_awaited_once_with(guild.id, channel.id)
            owner.send.assert_awaited_once()
            assert "has been deleted" in owner.send.call_args.args[0]
            mark_notified.assert_awaited_once_with(guild.id, channel.id)
            return

        mark_deleted.assert_not_awaited()
        mark_notified.assert_not_awaited()
        owner.send.assert_not_awaited()
        if outcome != "forbidden":
            http_delete.assert_not_awaited()

    asyncio.run(check())


@pytest.mark.parametrize("missing", ["view_channel", "manage_channels", "manage_roles"])
def test_creation_reports_missing_category_permission_before_creating(
    import_app,
    monkeypatch,
    missing,
):
    module = import_app("app.services.ghosty_tempchannel_service")

    async def check():
        service, guild, owner, category = prepare_creation(module, monkeypatch)
        setattr(category.permissions_for.return_value, missing, False)
        with pytest.raises(module.TempChannelError, match="I need") as error:
            await service.create_channel(owner, "temporary", "1h")
        assert {
            "view_channel": "View Channel",
            "manage_channels": "Manage Channels",
            "manage_roles": "Manage Permissions (Manage Roles)",
        }[missing] in str(error.value)
        guild.create_text_channel.assert_not_awaited()
        module.repository.create_channel_record.assert_not_awaited()

    asyncio.run(check())


@pytest.mark.parametrize("step", ["create the channel", "configure channel permissions"])
def test_creation_explains_discord_denial_and_rolls_back(import_app, monkeypatch, step):
    module = import_app("app.services.ghosty_tempchannel_service")

    async def check():
        service, guild, owner, category = prepare_creation(module, monkeypatch)
        denied = discord.Forbidden(
            SimpleNamespace(status=403, reason="Forbidden"),
            {"message": "Missing Permissions", "code": 50013},
        )
        channel = MagicMock(spec=discord.TextChannel)
        channel.overwrites = {}
        channel.edit = AsyncMock(side_effect=denied)
        channel.delete = AsyncMock()
        if step == "create the channel":
            guild.create_text_channel.side_effect = denied
        else:
            guild.create_text_channel.return_value = channel

        with pytest.raises(module.TempChannelError, match=step) as error:
            await service.create_channel(owner, "temporary", "1h")
        assert error.value.__cause__ is denied
        assert "Manage Permissions" in str(error.value)
        module.repository.create_channel_record.assert_not_awaited()
        if step == "configure channel permissions":
            channel.delete.assert_awaited_once()
        else:
            channel.delete.assert_not_awaited()

    asyncio.run(check())


def test_existing_channel_policy_is_applied_without_repeated_edits(import_app):
    module = import_app("app.services.ghosty_tempchannel_service")

    async def check():
        guild = make_guild()
        member = make_member(guild, 100, [2])
        old = make_channel(
            guild,
            {
                guild.get_role(2): discord.PermissionOverwrite(use_application_commands=True),
                member: discord.PermissionOverwrite(
                    view_channel=True, use_application_commands=True
                ),
                discord.Object(id=500, type=discord.User): discord.PermissionOverwrite(
                    use_application_commands=True
                ),
            },
        )
        service = module.GhostyTempChannelService(None)
        with patch.object(discord.TextChannel, "edit", new=AsyncMock()) as edit:
            await service.ensure_command_permissions(old)
            updated = make_channel(guild, edit.call_args.kwargs["overwrites"])
            assert not updated.permissions_for(member).use_application_commands
            assert updated.permissions_for(member).view_channel
            await service.ensure_command_permissions(updated)
            edit.assert_awaited_once()

    asyncio.run(check())
