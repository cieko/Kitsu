from __future__ import annotations

import asyncio

import pytest

from .helpers import drain_background, prepare_live


def test_slow_deletion_blocks_new_trackers_until_the_previous_copy_is_gone(import_app, monkeypatch):
    module = import_app("app.cogs.ghosty")

    async def check():
        env = prepare_live(module, monkeypatch)
        await env.cog.on_message(env.message(1000))
        first = env.posts[200]
        entered, release = asyncio.Event(), asyncio.Event()

        async def slow_delete():
            entered.set()
            await release.wait()

        first.delete.side_effect = slow_delete
        replacement = asyncio.create_task(env.cog.on_message(env.message(1001)))
        following = None
        try:
            await asyncio.wait_for(entered.wait(), timeout=0.5)
            following = asyncio.create_task(env.cog.on_message(env.message(1002)))
            await asyncio.sleep(0)
            env.channels[2].send.assert_awaited_once()
            assert not replacement.done() and not following.done()
            release.set()
            await asyncio.wait_for(asyncio.gather(replacement, following), timeout=0.5)
            assert env.states[2]["tracker_message_id"] == "202"
            assert env.states[2]["streak"] == 5
            env.posts[202].delete.assert_not_awaited()
        finally:
            release.set()
            await asyncio.gather(replacement, *([following] if following else []))
            await drain_background(env.cog)

    asyncio.run(check())


def test_cog_unload_cancels_pending_delivery_jobs(import_app, monkeypatch):
    module = import_app("app.cogs.ghosty")

    async def check():
        env = prepare_live(module, monkeypatch)
        entered, release = asyncio.Event(), asyncio.Event()

        async def waiting(guild):
            entered.set()
            await release.wait()

        env.cog._refresh_dashboard_for_guild.side_effect = waiting
        await env.cog.on_message(env.message(1000))
        await entered.wait()
        pending = tuple(env.cog._background_tasks)
        env.cog.cog_unload()
        await asyncio.gather(*pending, return_exceptions=True)
        assert all(task.cancelled() for task in pending)
        assert not env.cog._dashboard_refresh_tasks

    asyncio.run(check())


@pytest.mark.parametrize(
    "net_result, label, reaction",
    [
        (-7, "loss", "Ghosty calls that generosity; the net loss politely disagrees. 🙃"),
        (1389, "win", "A welcome net win—Ghosty finally picked up the tab! 🎉"),
        (0, "break-even", "All that suspense just to break even—classic Ghosty. 😅"),
    ],
)
def test_final_summary_is_compact_mint_and_uses_the_streak_net_result(
    import_app, monkeypatch, net_result, label, reaction
):
    module = import_app("app.cogs.ghosty")

    async def check():
        env = prepare_live(module, monkeypatch)
        env.states[2].update(streak=17, streak_loss=4000 - net_result)
        module.live.get_hit_reaction.return_value = reaction
        await env.cog.on_message(env.message(1000, outcome="hit", wager=400))
        await drain_background(env.cog)
        sent = env.channels[2].send.call_args.kwargs["embed"]
        assert sent.colour.value == 0x7CC6B0
        assert sent.description.startswith(
            f"**Streak {label}:** {abs(net_result):,} Goos\n{reaction}"
        )
        if net_result <= 0:
            assert "🎉" not in sent.description and "Hurrah" not in sent.description
        final = next(post.embed for post in env.posts.values() if post.embed is not None)
        assert final.description == (
            f"**Streak {label}:** {abs(net_result):,} Goos\n{reaction}\n\n"
            "**Next Estimated Hit @** `10`"
        )
        assert (
            not {"author", "title", "url", "footer", "timestamp", "fields"} & final.to_dict().keys()
        )
        assert all(
            text not in final.description
            for text in ("received", "Wager:", "Streak ended:", "<#2>", "<@7>")
        )
        module.live.get_hit_reaction.assert_called_once_with(net_result=net_result)
        next(
            post for post in env.posts.values() if post.embed is not None
        ).edit.assert_awaited_once()
        env.channels[2].send.assert_awaited_once()
        market = env.channels[5].send.call_args.kwargs
        assert "Kudos to <@7>" in market["content"] and "embed" not in market

    asyncio.run(check())


@pytest.mark.parametrize("net_result", [-7, 0, 1389])
def test_missing_message_bank_still_posts_a_complete_local_summary(
    import_app, monkeypatch, tmp_path, net_result
):
    module = import_app("app.cogs.ghosty")
    reactions = import_app("app.services.ghosty_reaction_service")
    monkeypatch.setattr(reactions, "REACTIONS_FILE", tmp_path / "missing.json")
    reactions.load_hit_reactions.cache_clear()

    async def check():
        env = prepare_live(module, monkeypatch)
        env.states[2]["streak_loss"] = 4000 - net_result
        monkeypatch.setattr(module.live, "get_hit_reaction", reactions.get_hit_reaction)
        await env.cog.on_message(env.message(1000, outcome="hit", wager=400))
        await drain_background(env.cog)
        initial = env.channels[2].send.call_args.kwargs["embed"]
        assert reactions.get_hit_reaction(net_result=net_result) in initial.description
        assert "Preparing a reaction" not in initial.description
        assert "1564713766220865617" in initial.description
        announcement = env.posts[max(env.posts)]
        announcement.edit.assert_awaited_once()
        assert "1564713766220865617" not in announcement.embed.description
        assert announcement.embed.colour.value == 0x7CC6B0
        env.channels[2].send.assert_awaited_once()

    try:
        asyncio.run(check())
    finally:
        reactions.load_hit_reactions.cache_clear()
