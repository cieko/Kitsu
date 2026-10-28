from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord


def prepare_live(module, monkeypatch):
    channels = {}
    posts = {}
    operations = []
    config = {"dashboard_channel_id": "5", "minimum_samples": 50}
    states = {
        channel_id: {"streak": 2, "streak_loss": 222, "total_gambles": 100}
        for channel_id in (2, 3, 5)
    }
    for channel_id in states:
        channel = MagicMock(spec=discord.TextChannel)
        channel.id, channel.name, channel.mention = (
            channel_id,
            f"gamble-{channel_id}",
            f"<#{channel_id}>",
        )
        channels[channel_id] = channel

        async def send(*args, _channel_id=channel_id, **kwargs):
            await asyncio.sleep(0)
            message_id = 200 + len(posts)
            result = SimpleNamespace(id=message_id, embed=kwargs.get("embed"))
            result.delete = AsyncMock(side_effect=lambda: operations.append(("delete", message_id)))
            result.edit = AsyncMock(
                side_effect=lambda **changes: setattr(result, "embed", changes["embed"])
            )
            posts[message_id] = result
            operations.append(("send", _channel_id, message_id))
            return result

        channel.send = AsyncMock(side_effect=send)
        channel.get_partial_message.side_effect = lambda message_id: posts[message_id]

    guild = SimpleNamespace(id=1, name="Ninetails Market", get_channel=channels.get)
    bot = SimpleNamespace(user=SimpleNamespace(id=900))
    monkeypatch.setattr(
        module.repository,
        "get_config",
        AsyncMock(
            side_effect=lambda guild_id, key, default=None: config.get(key, default),
        ),
    )
    monkeypatch.setattr(
        module.live,
        "get_application_emoji",
        AsyncMock(return_value="<a:loading:1564713766220865617>"),
    )
    monkeypatch.setattr(
        module.live,
        "get_hit_reaction",
        MagicMock(return_value="Hurrah! Ghosty says you're welcome! 🎉"),
    )

    async def save_tracker(guild_id, channel_id, message_id):
        states[channel_id]["tracker_message_id"] = str(message_id)
        operations.append(("save", channel_id, message_id))

    monkeypatch.setattr(
        module.repository, "set_tracker_message", AsyncMock(side_effect=save_tracker)
    )
    records = {}

    async def process(message):
        await asyncio.sleep(0)
        if message.id in records or getattr(message, "ignored", False):
            return None
        stats = states[message.channel.id]
        last_streak, last_loss = stats["streak"], stats["streak_loss"]
        hit = message.outcome == "hit"
        stats.update(
            streak=0 if hit else last_streak + 1,
            streak_loss=0 if hit else last_loss + message.wager,
            total_gambles=stats["total_gambles"] + 1,
        )
        if hit:
            stats.pop("tracker_message_id", None)
        recorded = {
            "guild_id": 1,
            "channel_id": message.channel.id,
            "user_id": 7,
            "result_message_id": message.id,
            "outcome": message.outcome,
            "created_at": message.created_at,
            "wager": message.wager,
            "payout": message.wager * 10 if hit else 0,
            "stats": {
                **stats,
                "last_streak": last_streak,
                "streak_net_pl": message.wager * 10 - last_loss if hit else -message.wager,
            },
        }
        records[message.id] = recorded
        return recorded

    def make_cog():
        cog = module.GhostyCog(bot)
        cog.service.process_message = AsyncMock(side_effect=process)
        cog.service.get_live_prediction_data = MagicMock(
            side_effect=lambda guild_id, channel_id, **kwargs: {
                "ready": True,
                "samples": states[channel_id]["total_gambles"],
                "hit_at": states[channel_id]["streak"] + 10,
                "hit_percent": 10.0,
            }
        )
        cog.service.get_prediction_data = AsyncMock(return_value={"ready": True, "hit_at": 10})
        cog._refresh_dashboard_for_guild = AsyncMock(return_value=True)
        cog._get_streak_participant_ids = AsyncMock(return_value=[11, 12])
        return cog

    def message(
        message_id, channel_id=2, outcome="miss", wager=144, author_id=module.GHOSTY_BOT_ID
    ):
        return SimpleNamespace(
            id=message_id,
            guild=guild,
            channel=channels[channel_id],
            author=SimpleNamespace(id=author_id),
            created_at=datetime(2026, 10, 27, tzinfo=timezone.utc),
            jump_url=f"https://discord.com/channels/1/{channel_id}/{message_id}",
            outcome=outcome,
            wager=wager,
        )

    return SimpleNamespace(
        cog=make_cog(),
        make_cog=make_cog,
        message=message,
        channels=channels,
        posts=posts,
        states=states,
        operations=operations,
        config=config,
        records=records,
    )


async def drain_background(cog):
    while cog._background_tasks:
        await asyncio.gather(*tuple(cog._background_tasks), return_exceptions=True)
        await asyncio.sleep(0)
