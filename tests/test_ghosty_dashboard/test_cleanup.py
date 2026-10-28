import asyncio
from types import SimpleNamespace

import discord
import pytest

from .helpers import prepare_dashboard


def test_partial_stack_must_be_deleted_before_retry_posts_anything(import_app, monkeypatch):
    module = import_app("app.cogs.ghosty")

    async def check():
        cog, guild, channel, messages, config = prepare_dashboard(module, monkeypatch)
        save = module.repository.set_config.side_effect
        send = channel.send.side_effect
        error = discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "Cannot delete")

        def send_with_failed_cleanup(*args, **kwargs):
            message = send(*args, **kwargs)
            message.delete.side_effect = error
            return message

        channel.send.side_effect = send_with_failed_cleanup
        module.repository.set_config.side_effect = RuntimeError("database unavailable")
        with pytest.raises(RuntimeError):
            await cog._refresh_dashboard_for_guild(guild, repost=True)
        channel.send.assert_awaited_once()
        assert not await cog._refresh_dashboard_for_guild(guild)
        channel.send.assert_awaited_once()
        remaining = cog._dashboard_pending_cleanup[guild.id][0]
        remaining.delete.side_effect = lambda: messages.pop(remaining.id, None)
        channel.send.side_effect = send
        module.repository.set_config.side_effect = save
        assert await cog._refresh_dashboard_for_guild(guild)
        assert len(messages) == 3
        assert guild.id not in cog._dashboard_pending_cleanup

    asyncio.run(check())
