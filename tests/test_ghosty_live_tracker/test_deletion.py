import asyncio
from types import SimpleNamespace

import discord

from .helpers import drain_background, prepare_live


def test_failed_tracker_deletion_prevents_another_send_and_retries(import_app, monkeypatch):
    module = import_app("app.cogs.ghosty")

    async def check():
        env = prepare_live(module, monkeypatch)
        await env.cog.on_message(env.message(1000))
        previous = env.posts[200]
        previous.delete.side_effect = discord.Forbidden(
            SimpleNamespace(status=403, reason="Forbidden"),
            "Cannot delete",
        )
        await env.cog.on_message(env.message(1001))
        env.channels[2].send.assert_awaited_once()
        assert env.states[2]["tracker_message_id"] == "200"
        previous.delete.side_effect = lambda: env.operations.append(("delete", 200))
        await env.cog.on_message(env.message(1002))
        await drain_background(env.cog)
        assert env.operations[-3:] == [("delete", 200), ("send", 2, 201), ("save", 2, 201)]

    asyncio.run(check())
