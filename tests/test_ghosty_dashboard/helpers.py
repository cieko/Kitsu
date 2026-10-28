from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord


def prepare_dashboard(module, monkeypatch):
    channel = MagicMock(spec=discord.TextChannel)
    channel.id, channel.mention, channel.name, channel.position = 5, "<#5>", "tracker", 0
    channel.permissions_for.return_value = discord.Permissions(view_channel=True)
    guild = SimpleNamespace(
        id=1,
        text_channels=[channel],
        get_member=lambda member_id: None,
        get_channel=lambda channel_id: channel if channel_id == 5 else None,
    )
    messages = {
        message_id: SimpleNamespace(
            id=message_id,
            edit=AsyncMock(),
            delete=AsyncMock(),
            flags=SimpleNamespace(components_v2=True),
            author=SimpleNamespace(id=900),
            content=module.DASHBOARD_HEADING if message_id == 10 else "",
        )
        for message_id in (10, 20, 30)
    }
    channel.operations = []

    def prepare_delete(message):
        async def delete():
            channel.operations.append(("delete", message.id))
            messages.pop(message.id, None)

        message.delete = AsyncMock(side_effect=delete)

    for message in messages.values():
        prepare_delete(message)
    next_message_id = max(messages)

    def fetch_message(message_id):
        if message_id not in messages:
            raise discord.NotFound(
                SimpleNamespace(status=404, reason="Not Found"),
                {"message": "Unknown Message", "code": 10008},
            )
        return messages[message_id]

    async def history(*, limit, before):
        candidates = sorted(
            (message for message in messages.values() if message.id < before.id),
            key=lambda message: message.id,
            reverse=True,
        )
        for message in candidates[:limit]:
            yield message

    def send(*args, **kwargs):
        nonlocal next_message_id
        next_message_id = max(next_message_id, max(messages, default=0)) + 10
        message_id = next_message_id
        message = SimpleNamespace(
            id=message_id,
            edit=AsyncMock(),
            delete=AsyncMock(),
            guild=guild,
            channel=channel,
            author=SimpleNamespace(id=900),
            content=args[0] if args else kwargs.get("content", ""),
            flags=SimpleNamespace(components_v2=True),
        )
        prepare_delete(message)
        messages[message_id] = message
        channel.operations.append(("send", message_id))
        return message

    channel.fetch_message = AsyncMock(side_effect=fetch_message)
    channel.history = MagicMock(side_effect=history)
    channel.send = AsyncMock(side_effect=send)
    config = {
        "dashboard_channel_id": "5",
        "dashboard_tracker_message_id": "10",
        "dashboard_message_id": "20",
        "dashboard_controls_message_id": "30",
    }
    monkeypatch.setattr(
        module.repository,
        "get_config",
        AsyncMock(
            side_effect=lambda guild_id, key, default=None: config.get(key, default),
        ),
    )
    monkeypatch.setattr(
        module.repository,
        "set_config",
        AsyncMock(
            side_effect=lambda guild_id, key, value: config.__setitem__(key, value),
        ),
    )
    cog = module.GhostyCog(SimpleNamespace(guilds=[guild], user=SimpleNamespace(id=900)))
    live_view = discord.ui.LayoutView(timeout=None)
    live_view.add_item(discord.ui.TextDisplay("Live statistics"))
    cog._build_dashboard_view = AsyncMock(return_value=live_view)
    cog._build_dashboard_controls_view = MagicMock(wraps=cog._build_dashboard_controls_view)
    return cog, guild, channel, messages, config


def add_market_message(guild, channel, messages, *, message_id=35, author_id=7, **permissions):
    message = SimpleNamespace(
        id=message_id,
        guild=guild,
        channel=channel,
        content="Market update",
        author=SimpleNamespace(id=author_id, guild_permissions=discord.Permissions(**permissions)),
        delete=AsyncMock(),
        flags=SimpleNamespace(components_v2=False),
        jump_url=f"https://discord.com/channels/{guild.id}/{channel.id}/{message_id}",
    )
    messages[message.id] = message
    return message


async def finish_background(cog):
    while cog._background_tasks:
        await asyncio.gather(*tuple(cog._background_tasks), return_exceptions=True)
        await asyncio.sleep(0)
