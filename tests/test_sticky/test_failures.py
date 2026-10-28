import asyncio
from types import SimpleNamespace

import discord
import pytest

from .helpers import environment, interaction


@pytest.mark.parametrize("pointer", [None, 50])
@pytest.mark.parametrize("embedded", [False, True])
def test_restart_adopts_delivered_copy_without_posting(import_app, monkeypatch, pointer, embedded):
    module = import_app("app.cogs.sticky")

    async def check():
        env = environment(module, monkeypatch)
        env.state["messages"].clear()
        env.state["row"]["last_message_id"] = pointer
        if embedded:
            env.state["row"]["message_script"] = "{embed}{title: Rules}{description: Be kind}"
        content, embed = env.cog.sticky_service.render(
            env.state["row"]["message_script"], env.guild
        )
        env.message(1001, content=content, embeds=[embed] if embed else [])
        await env.cog.reconcile_stickies()
        await env.cog.reconcile_stickies()
        env.channel.send.assert_not_awaited()
        assert env.state["row"]["last_message_id"] == 1001
        assert list(env.state["messages"]) == [1001]

    asyncio.run(check())


def test_newer_orphan_is_adopted_and_old_copy_deleted_before_any_send(import_app, monkeypatch):
    module = import_app("app.cogs.sticky")

    async def check():
        env = environment(module, monkeypatch)
        env.message(1001)
        await env.cog.on_message(env.message(60, author=SimpleNamespace(id=8, bot=False)))
        env.channel.send.assert_not_awaited()
        assert env.state["row"]["last_message_id"] == 1001
        assert env.state["deleted"] == [50]

    asyncio.run(check())


@pytest.mark.parametrize("failed_read", ["fetch", "history"])
def test_read_errors_do_not_prove_a_sticky_is_missing(import_app, monkeypatch, failed_read):
    module = import_app("app.cogs.sticky")

    async def check():
        env = environment(module, monkeypatch)
        error = discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "Cannot read")
        if failed_read == "fetch":
            env.channel.fetch_message.side_effect = error
        else:

            async def inaccessible_history(**kwargs):
                raise error
                yield

            env.channel.history = inaccessible_history
        await env.cog.reconcile_stickies()
        env.channel.send.assert_not_awaited()
        assert env.state["deleted"] == []

    asyncio.run(check())


def test_send_with_lost_acknowledgement_is_recovered_after_restart(import_app, monkeypatch):
    module = import_app("app.cogs.sticky")

    async def check():
        env = environment(module, monkeypatch)
        send = env.channel.send.side_effect

        async def uncertain_send(**kwargs):
            await send(**kwargs)
            raise discord.HTTPException(
                SimpleNamespace(status=500, reason="Server Error"), "Lost acknowledgement"
            )

        env.channel.send.side_effect = uncertain_send
        await env.cog.on_message(env.message(60, author=SimpleNamespace(id=8, bot=False)))
        assert env.state["deleted"] == [50]
        restarted = module.Sticky(env.bot)
        await restarted.reconcile_stickies()
        assert env.state["row"]["last_message_id"] == 1001
        env.channel.send.assert_awaited_once()

    asyncio.run(check())


def test_legacy_pending_cleanup_blocks_send_until_deletion_succeeds(import_app, monkeypatch):
    module = import_app("app.cogs.sticky")

    async def check():
        env = environment(module, monkeypatch)
        env.message(1001)
        env.state["row"].update(last_message_id=1001, pending_delete_id=50)
        env.state["delete_error"] = 50
        await env.cog.reconcile_stickies()
        env.channel.send.assert_not_awaited()
        assert env.state["row"]["pending_delete_id"] == 50
        env.state["delete_error"] = None
        await env.cog.reconcile_stickies()
        env.channel.send.assert_not_awaited()
        assert list(env.state["messages"]) == [1001]
        assert "pending_delete_id" not in env.state["row"]

    asyncio.run(check())


def test_remove_also_recovers_an_unsaved_message(import_app, monkeypatch):
    module = import_app("app.cogs.sticky")

    async def check():
        env = environment(module, monkeypatch)
        env.state["messages"].clear()
        env.message(1001)
        await env.cog.remove.callback(env.cog, interaction(env), env.channel)
        assert env.state["messages"] == {} and env.state["row"] is None
        env.channel.send.assert_not_awaited()

    asyncio.run(check())
