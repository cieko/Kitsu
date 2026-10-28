import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord


def test_private_channels_revoke_inherited_grants_and_keep_allowed_members(
    import_app, monkeypatch
):
    module = import_app("app.services.ghosty_tempchannel_service")

    async def check():
        guild = SimpleNamespace(id=1, owner_id=999)

        def role(role_id, permissions=0):
            return discord.Role(
                guild=guild, state=MagicMock(),
                data={"id": str(role_id), "name": str(role_id), "permissions": str(permissions)},
            )

        everyone = role(1, discord.Permissions(
            view_channel=True, send_messages=True, read_message_history=True,
            use_application_commands=True,
        ).value)
        ordinary_role, staff_role = role(2), role(3)
        roles = {item.id: item for item in (everyone, ordinary_role, staff_role)}
        guild.default_role = everyone
        guild.get_role = roles.get

        def member(member_id, role_ids=()):
            target = MagicMock(spec=discord.Member)
            target.id, target.guild = member_id, guild
            target._roles = discord.utils.SnowflakeList(role_ids)
            target.is_timed_out.return_value = False
            return target

        owner = member(100)
        outsider = member(101, [2])
        invited = member(102, [2])
        direct_outsider = member(103)
        uncached_invited = member(104, [2])
        staff = member(105, [3])
        uncached_outsider = member(106)
        bot = member(900)
        members = {item.id: item for item in (owner, outsider, invited, direct_outsider, staff, bot)}
        guild.get_member = members.get
        guild.me = bot

        def channel_from_overwrites(overwrites):
            payload = []
            for target, overwrite in overwrites.items():
                allow, deny = overwrite.pair()
                is_role = isinstance(target, discord.Role) or (
                    isinstance(target, discord.Object) and target.type is discord.Role
                )
                payload.append({
                    "id": str(target.id), "type": 0 if is_role else 1,
                    "allow": str(allow.value), "deny": str(deny.value),
                })
            return discord.TextChannel(
                state=MagicMock(), guild=guild,
                data={"id": "50", "type": 0, "name": "temporary", "position": 0,
                      "permission_overwrites": payload},
            )

        channel = channel_from_overwrites({
            ordinary_role: discord.PermissionOverwrite(
                view_channel=True, attach_files=False, use_application_commands=True
            ),
            direct_outsider: discord.PermissionOverwrite(
                view_channel=True, use_application_commands=True
            ),
            discord.Object(id=106, type=discord.User): discord.PermissionOverwrite(view_channel=True),
            discord.Object(id=4, type=discord.Role): discord.PermissionOverwrite(view_channel=True),
            owner: discord.PermissionOverwrite(manage_messages=False),
        })
        service = module.GhostyTempChannelService(SimpleNamespace())
        service._get_owned_channel = AsyncMock(return_value={
            "creator_id": "100", "member_ids": ["102", "104"]
        })
        service._get_staff_roles = AsyncMock(return_value=[staff_role])
        update = AsyncMock()
        monkeypatch.setattr(module.repository, "update_visibility", update)

        with patch.object(discord.TextChannel, "edit", new=AsyncMock()) as edit:
            await service.set_visibility(owner, channel, "private")
            private_overwrites = edit.call_args.kwargs["overwrites"]
        private = channel_from_overwrites(private_overwrites)

        for target in (outsider, direct_outsider, uncached_outsider):
            assert not private.permissions_for(target).view_channel
        for target in (owner, invited, uncached_invited, staff, bot):
            permissions = private.permissions_for(target)
            assert permissions.view_channel
            assert permissions.send_messages
            assert permissions.read_message_history
            assert not permissions.use_application_commands
        assert private.permissions_for(staff).manage_channels
        assert private_overwrites[ordinary_role].attach_files is False
        assert private_overwrites[owner].manage_messages is False
        assert not any(
            target.id == 4 and overwrite.view_channel is True
            for target, overwrite in private_overwrites.items()
        )
        update.assert_awaited_once_with(1, 50, "private")

        # Reopening must not leave ordinary roles denied by the privacy change.
        with patch.object(discord.TextChannel, "edit", new=AsyncMock()) as edit:
            await service.set_visibility(owner, private, "public")
            public = channel_from_overwrites(edit.call_args.kwargs["overwrites"])
        for target in (outsider, direct_outsider, uncached_outsider):
            assert public.permissions_for(target).view_channel
        for target in (owner, invited, uncached_invited, staff, bot, outsider, direct_outsider):
            assert not public.permissions_for(target).use_application_commands

    asyncio.run(check())
