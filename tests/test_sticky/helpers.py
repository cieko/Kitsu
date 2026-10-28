from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord


def environment(module, monkeypatch):
    bot_user = SimpleNamespace(id=900, bot=True)
    guild = SimpleNamespace(id=1, name="Market", icon=None, me=bot_user)
    channel = MagicMock(spec=discord.TextChannel)
    channel.id, channel.guild, channel.name, channel.mention = 2, guild, "chat", "<#2>"
    permissions = discord.Permissions(
        view_channel=True, send_messages=True, read_message_history=True, embed_links=True
    )
    channel.permissions_for.return_value = permissions
    guild.get_channel = lambda channel_id: channel if channel_id == 2 else None
    bot = SimpleNamespace(
        user=bot_user, main_guild_id=1, get_channel=guild.get_channel, wait_until_ready=AsyncMock()
    )
    state = {
        "row": {
            "guild_id": 1,
            "channel_id": 2,
            "message_script": "Old sticky",
            "active": True,
            "ignore_bots": False,
            "last_message_id": 50,
        },
        "messages": {},
        "next_id": 1000,
        "deleted": [],
        "operations": [],
        "send_error": None,
        "save_error": None,
        "delete_error": None,
    }

    def make_message(message_id, *, author=bot_user, content="Old sticky", embeds=None):
        message = SimpleNamespace(
            id=message_id,
            author=author,
            guild=guild,
            channel=channel,
            content=content,
            embeds=embeds or [],
        )

        async def delete():
            if state["delete_error"] == message_id:
                raise discord.Forbidden(
                    SimpleNamespace(status=403, reason="Forbidden"), "Cannot delete"
                )
            if message_id not in state["messages"]:
                raise discord.NotFound(
                    SimpleNamespace(status=404, reason="Not Found"), "Unknown message"
                )
            state["deleted"].append(message_id)
            state["operations"].append(("delete", message_id))
            del state["messages"][message_id]

        message.delete = AsyncMock(side_effect=delete)
        state["messages"][message_id] = message
        return message

    make_message(50)

    async def send(**kwargs):
        assert not any(message.author.id == bot_user.id for message in state["messages"].values())
        if state["send_error"]:
            raise state["send_error"]
        state["next_id"] += 1
        state["operations"].append(("send", state["next_id"]))
        return make_message(
            state["next_id"],
            content=kwargs.get("content") or "",
            embeds=[kwargs["embed"]] if kwargs.get("embed") else [],
        )

    async def fetch(message_id):
        if message_id not in state["messages"]:
            raise discord.NotFound(
                SimpleNamespace(status=404, reason="Not Found"), "Unknown message"
            )
        return state["messages"][message_id]

    async def history(*, limit, after=None, oldest_first=False):
        rows = sorted(
            state["messages"].values(), key=lambda message: message.id, reverse=not oldest_first
        )
        for message in [message for message in rows if after is None or message.id > after.id][
            :limit
        ]:
            yield message

    channel.send = AsyncMock(side_effect=send)
    channel.fetch_message = AsyncMock(side_effect=fetch)

    def partial(message_id):
        async def delete():
            await (await fetch(message_id)).delete()

        return SimpleNamespace(delete=AsyncMock(side_effect=delete))

    channel.get_partial_message.side_effect = partial

    channel.history = history
    monkeypatch.setattr(
        module.repository, "get_sticky", AsyncMock(side_effect=lambda *args: deepcopy(state["row"]))
    )
    monkeypatch.setattr(
        module.repository,
        "get_stickies",
        AsyncMock(side_effect=lambda *args: [deepcopy(state["row"])] if state["row"] else []),
    )

    async def configure(guild_id, channel_id, message_script, created_by=None):
        if state["row"] is None:
            state["row"] = {
                "guild_id": guild_id,
                "channel_id": channel_id,
                "last_message_id": None,
                "ignore_bots": True,
            }
        state["row"].update(message_script=message_script, created_by=created_by, active=True)
        return True

    async def save_id(guild_id, channel_id, message_id, *, pending_delete_id=None):
        if state["save_error"]:
            raise state["save_error"]
        if state["row"] is None:
            return False
        state["row"].update(last_message_id=message_id, pending_delete_id=pending_delete_id)
        return True

    async def clear(guild_id, channel_id, message_id):
        if state["row"] and state["row"].get("pending_delete_id") == message_id:
            state["row"].pop("pending_delete_id")

    async def active(guild_id, channel_id, value):
        if state["row"] is None:
            return False
        state["row"]["active"] = value
        return True

    async def delete(*args):
        state["row"] = None
        return True

    monkeypatch.setattr(module.repository, "set_sticky", AsyncMock(side_effect=configure))
    monkeypatch.setattr(
        module.repository, "update_sticky_message_id", AsyncMock(side_effect=save_id)
    )
    monkeypatch.setattr(module.repository, "clear_sticky_cleanup", AsyncMock(side_effect=clear))
    monkeypatch.setattr(module.repository, "set_sticky_active", AsyncMock(side_effect=active))
    monkeypatch.setattr(module.repository, "delete_sticky", AsyncMock(side_effect=delete))
    return SimpleNamespace(
        cog=module.Sticky(bot),
        bot=bot,
        guild=guild,
        channel=channel,
        state=state,
        message=make_message,
        permissions=permissions,
    )


def interaction(env, *, admin=True):
    member = MagicMock(spec=discord.Member)
    member.id, member.guild, member.guild_permissions = (
        7,
        env.guild,
        discord.Permissions(administrator=admin),
    )
    acknowledged = {"value": False}

    async def acknowledge(*args, **kwargs):
        acknowledged["value"] = True

    return SimpleNamespace(
        guild=env.guild,
        user=member,
        channel=env.channel,
        data={},
        response=SimpleNamespace(
            defer=AsyncMock(side_effect=acknowledge),
            is_done=lambda: acknowledged["value"],
            send_message=AsyncMock(side_effect=acknowledge),
            edit_message=AsyncMock(),
        ),
        followup=SimpleNamespace(send=AsyncMock()),
        edit_original_response=AsyncMock(),
    )
