from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord


def make_guild():
    guild = SimpleNamespace(id=1, owner_id=999)
    roles = {}
    for role_id in (1, 2, 3):
        roles[role_id] = discord.Role(
            guild=guild,
            state=MagicMock(),
            data={
                "id": str(role_id),
                "name": str(role_id),
                "position": role_id,
                "permissions": str(
                    discord.Permissions(
                        view_channel=True,
                        send_messages=True,
                        read_message_history=True,
                        use_application_commands=True,
                    ).value
                ),
            },
        )
    guild.default_role = roles[1]
    guild.get_role = roles.get
    guild.members_by_id = {}
    guild.get_member = guild.members_by_id.get
    return guild


def make_member(guild, user_id, role_ids=()):
    member = MagicMock(spec=discord.Member)
    member.id, member.guild, member.bot = user_id, guild, False
    member._roles = discord.utils.SnowflakeList(role_ids)
    member.roles = [guild.default_role] + [guild.get_role(role_id) for role_id in role_ids]
    member.guild_permissions = discord.Permissions.none()
    member.is_timed_out.return_value = False
    guild.members_by_id[user_id] = member
    return member


def make_channel(guild, overwrites, *, state=None):
    payload = []
    for target, overwrite in overwrites.items():
        allow, deny = overwrite.pair()
        is_role = isinstance(target, discord.Role) or (
            isinstance(target, discord.Object) and target.type is discord.Role
        )
        payload.append(
            {
                "id": str(target.id),
                "type": 0 if is_role else 1,
                "allow": str(allow.value),
                "deny": str(deny.value),
            }
        )
    return discord.TextChannel(
        state=state if state is not None else MagicMock(),
        guild=guild,
        data={
            "id": "50",
            "type": 0,
            "name": "temporary",
            "position": 0,
            "permission_overwrites": payload,
        },
    )


def prepare_creation(module, monkeypatch):
    guild = make_guild()
    owner = make_member(guild, 100, [2])
    guild.me = make_member(guild, 900)
    category = MagicMock(spec=discord.CategoryChannel)
    category.id, category.name = 77, "Temporary channels"
    category.overwrites = {}
    category.permissions_for.return_value = discord.Permissions(
        view_channel=True,
        manage_channels=True,
        manage_roles=True,
    )
    guild.get_channel = lambda channel_id: category
    guild.create_text_channel = AsyncMock()
    service = module.GhostyTempChannelService(None)
    service.initialize = AsyncMock()
    service.get_settings = AsyncMock(
        return_value={
            "enabled": True,
            "category_id": 77,
            "staff_role_ids": ["3"],
        }
    )
    monkeypatch.setattr(module.repository, "get_active_creator_count", AsyncMock(return_value=0))
    monkeypatch.setattr(module.repository, "get_active_role_count", AsyncMock(return_value=0))
    monkeypatch.setattr(module.repository, "create_channel_record", AsyncMock(return_value=True))
    return service, guild, owner, category
