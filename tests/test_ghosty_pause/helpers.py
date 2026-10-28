from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
from pymongo.errors import AutoReconnect


def prepare(module, monkeypatch):
    guild = SimpleNamespace(id=1, name="Test server", owner_id=999, chunked=True)
    roles = {}
    for role_id in (1, 2, module.PAUSE_MOD_ROLE_ID, 4):
        permission = discord.Permissions(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            use_application_commands=True,
        )
        if role_id == 4:
            permission.manage_channels = permission.manage_roles = True
        roles[role_id] = discord.Role(
            guild=guild,
            state=MagicMock(),
            data={
                "id": str(role_id),
                "name": str(role_id),
                "position": 1,
                "permissions": str(permission.value),
            },
        )
    guild.default_role, guild.get_role = roles[1], roles.get
    members = {}
    for member_id, member_roles in (
        (100, [2]),
        (101, [2]),
        (102, [module.PAUSE_MOD_ROLE_ID]),
        (103, [2]),
        (900, [4]),
        (module.GHOSTY_BOT_ID, [2]),
    ):
        member = MagicMock(spec=discord.Member)
        member.id, member.guild = member_id, guild
        member.bot = member_id in {900, module.GHOSTY_BOT_ID}
        member.name = member.display_name = f"player{member_id}"
        member._roles = discord.utils.SnowflakeList(member_roles)
        member.roles = [roles[1], *[roles[role_id] for role_id in member_roles]]
        member.guild_permissions = discord.Permissions.none()
        member.is_timed_out.return_value = False
        members[member_id] = member
    guild.get_member, guild.members, guild.me = members.get, list(members.values()), members[900]
    guild.fetch_member = AsyncMock(side_effect=lambda member_id: members[member_id])
    state = {
        "overwrites": {
            roles[1]: discord.PermissionOverwrite(
                view_channel=True, use_application_commands=False
            ),
            roles[2]: discord.PermissionOverwrite(send_messages=True, embed_links=True),
            members[100]: discord.PermissionOverwrite(use_application_commands=False),
            members[101]: discord.PermissionOverwrite(send_messages=True, attach_files=True),
        },
        "pause": None,
        "edits": [],
        "fail_before": False,
        "fail_after": False,
        "stats": {"streak": 7, "total_gambles": 100},
    }

    def fresh():
        payload = []
        for target, overwrite in state["overwrites"].items():
            allow, deny = overwrite.pair()
            payload.append(
                {
                    "id": str(target.id),
                    "type": 0 if module.is_role(target) else 1,
                    "allow": str(allow.value),
                    "deny": str(deny.value),
                }
            )
        return discord.TextChannel(
            state=MagicMock(),
            guild=guild,
            data={
                "id": "50",
                "type": 0,
                "name": "gambling",
                "position": 0,
                "permission_overwrites": payload,
            },
        )

    async def edit(channel, *, overwrites, reason=None):
        state["edits"].append(overwrites)
        if state["fail_before"]:
            raise discord.Forbidden(
                SimpleNamespace(status=403, reason="Forbidden"), "Missing Permissions"
            )
        state["overwrites"] = {
            target: discord.PermissionOverwrite.from_pair(*value.pair())
            for target, value in overwrites.items()
        }
        if state["fail_after"]:
            state["fail_after"] = False
            raise AutoReconnect("response lost after Discord applied the overwrite")
        return fresh()

    monkeypatch.setattr(discord.TextChannel, "edit", edit)
    guild.fetch_channel = AsyncMock(side_effect=lambda channel_id: fresh())
    guild.get_channel = lambda channel_id: fresh() if channel_id == 50 else None
    bot = SimpleNamespace(
        user=SimpleNamespace(id=900),
        get_guild=lambda guild_id: guild,
        get_channel=guild.get_channel,
        wait_until_ready=AsyncMock(),
    )
    tracker = module.GhostyService(bot)
    changed = AsyncMock()
    service = module.GhostyPauseService(bot, tracker, changed)
    config = {"dashboard_channel_id": "5", "dashboard_excluded_channels": []}
    rows = [
        {
            "outcome": "miss",
            "user_id": str(100 if i < 5 else 101),
            "attribution_verified": True,
            "result_message_id": str(1000 + i),
        }
        for i in range(7)
    ]
    monkeypatch.setattr(
        module.tracking,
        "get_config",
        AsyncMock(side_effect=lambda guild_id, key, default=None: config.get(key, default)),
    )
    monkeypatch.setattr(
        module.tracking, "get_channel_stats", AsyncMock(side_effect=lambda *args: state["stats"])
    )
    monkeypatch.setattr(
        module.repository,
        "get_current_streak",
        AsyncMock(side_effect=lambda *args, **kw: rows[: kw["streak"]]),
    )
    monkeypatch.setattr(
        module.repository,
        "get_pause",
        AsyncMock(side_effect=lambda *args: deepcopy(state["pause"])),
    )

    async def create(guild_id, channel_id, record):
        if state["pause"] is not None:
            return False
        state["pause"] = deepcopy(record)
        return True

    async def save(guild_id, channel_id, record, *, revision):
        if (
            state["pause"] is None
            or state["pause"]["id"] != record["id"]
            or state["pause"]["revision"] != revision
        ):
            return False
        state["pause"] = deepcopy(record)
        return True

    async def clear(guild_id, channel_id, pause_id, *, revision):
        if (
            state["pause"] is None
            or state["pause"]["id"] != pause_id
            or state["pause"]["revision"] != revision
        ):
            return False
        state["pause"] = None
        return True

    monkeypatch.setattr(module.repository, "create_pause", AsyncMock(side_effect=create))
    monkeypatch.setattr(module.repository, "save_pause", AsyncMock(side_effect=save))
    monkeypatch.setattr(module.repository, "clear_pause", AsyncMock(side_effect=clear))
    monkeypatch.setattr(
        module.repository,
        "get_all_pauses",
        AsyncMock(
            side_effect=lambda: (
                [{"guild_id": "1", "channel_id": "50", "pause": deepcopy(state["pause"])}]
                if state["pause"]
                else []
            )
        ),
    )
    return SimpleNamespace(
        service=service,
        guild=guild,
        members=members,
        roles=roles,
        state=state,
        fresh=fresh,
        bot=bot,
        changed=changed,
        rows=rows,
        config=config,
    )


def flags(channel):
    return {
        (0 if isinstance(target, discord.Role) else 1, target.id): overwrite.pair()
        for target, overwrite in channel.overwrites.items()
    }
