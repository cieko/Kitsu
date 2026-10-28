from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock
from urllib.parse import parse_qs, urlsplit

import discord
import pytest

from .helpers import drain_background, prepare_live


def test_misses_replace_only_the_previous_tracker_with_updated_predictions(import_app, monkeypatch):
    module = import_app("app.cogs.ghosty")
    emojis = import_app("app.services.emoji_services")
    monkeypatch.setitem(emojis._EMOJI_CACHE, "ghosty_track", "<a:ghosty_track:1564714687084634274>")

    async def check():
        env = prepare_live(module, monkeypatch)
        await env.cog.on_message(env.message(1000))
        first = env.posts[200]
        assert first.embed.description == (
            "**Streak: 3 ・ Total Loss: 366 Goos**\n\n"
            "**Estimated hit @** `13`\n"
            "**Next-gamble chance** is `10%`"
        )
        assert not first.embed.fields
        assert first.embed.thumbnail.url.startswith(
            "https://cdn.discordapp.com/emojis/1564714687084634274."
        )
        assert first.embed.thumbnail.url.count("?") == 1
        assert parse_qs(urlsplit(first.embed.thumbnail.url).query)["size"] == ["128"]
        assert first.embed.colour.value == 0xF5276F
        assert not {"author", "title", "url", "footer", "timestamp"} & first.embed.to_dict().keys()
        first.delete.assert_not_awaited()
        # Fresh cog: the previous tracker must be recovered from channel data.
        cog = env.make_cog()
        await cog.on_message(env.message(1001, wager=169))
        await drain_background(cog)
        second = env.posts[201]
        assert "Streak: 4 ・ Total Loss: 535 Goos" in second.embed.description
        assert "**Estimated hit @** `14`" in second.embed.description
        first.delete.assert_awaited_once()
        second.delete.assert_not_awaited()
        assert env.states[2]["tracker_message_id"] == "201"
        assert env.operations[-3:] == [("delete", 200), ("send", 2, 201), ("save", 2, 201)]
        env.cog._refresh_dashboard_for_guild.assert_awaited_once()
        cog._refresh_dashboard_for_guild.assert_awaited_once()

    asyncio.run(check())


def test_hit_preserves_the_last_tracker_and_next_streak_does_not_delete_it(import_app, monkeypatch):
    module = import_app("app.cogs.ghosty")

    async def check():
        env = prepare_live(module, monkeypatch)
        await env.cog.on_message(env.message(1000))
        final_tracker = env.posts[200]

        def celebrate(*, net_result):
            assert net_result == 21854
            return "Hurrah! Ghosty says you're welcome! 🎉"

        module.live.get_hit_reaction.side_effect = celebrate
        await env.cog.on_message(env.message(1001, outcome="hit", wager=2222))
        await drain_background(env.cog)
        final_tracker.delete.assert_not_awaited()
        assert "tracker_message_id" not in env.states[2]
        celebration = env.posts[max(env.posts)].embed
        assert celebration.description.startswith("**Streak win:** 21,854 Goos\nHurrah!")
        assert celebration.colour.value == 0x7CC6B0
        assert "<@7>" not in celebration.description
        assert (
            not {"author", "title", "url", "footer", "timestamp", "fields"}
            & celebration.to_dict().keys()
        )
        assert "Ghosty says you're welcome" in celebration.description
        env.channels[5].send.assert_awaited_once()
        allowed = env.channels[5].send.call_args.kwargs["allowed_mentions"]
        assert not allowed.everyone and not allowed.roles
        assert {user.id for user in allowed.users} == {7, 11, 12}
        market = env.channels[5].send.call_args.kwargs
        assert market["content"] == (
            "<#2> <@11> <@12>\n"
            "🎉  **A Ghosty hit has been recorded!** Kudos to <@7>\n"
            "Total streak: 3 ・ Total profit: 21,854 Goos\n"
            "-# [Jump to message](https://discord.com/channels/1/2/1001)"
        )
        assert "embed" not in market and market["suppress_embeds"] is True
        assert market["content"].count("<@7>") == 1
        assert env.channels[2].send.call_args.kwargs["allowed_mentions"].users is False
        restarted = env.make_cog()
        await restarted.on_message(env.message(1002, wager=400))
        final_tracker.delete.assert_not_awaited()
        latest = env.channels[2].send.call_args.kwargs["embed"]
        assert "Streak: 1 ・ Total Loss: 400 Goos" in latest.description

    asyncio.run(check())


def test_channel_trackers_are_independent_under_concurrent_results(import_app, monkeypatch):
    module = import_app("app.cogs.ghosty")

    async def check():
        env = prepare_live(module, monkeypatch)
        await asyncio.gather(
            *[env.cog.on_message(env.message(1000 + i, wager=100)) for i in range(3)],
            env.cog.on_message(env.message(2000, channel_id=3)),
        )
        await drain_background(env.cog)
        assert env.states[2]["streak"] == 5 and env.states[2]["streak_loss"] == 522
        assert env.states[3]["streak"] == 3 and env.states[3]["streak_loss"] == 366
        for channel_id in (2, 3):
            active_id = int(env.states[channel_id]["tracker_message_id"])
            for operation in env.operations:
                if operation[0] == "send" and operation[1] == channel_id:
                    posted = env.posts[operation[2]]
                    assert posted.delete.await_count == int(posted.id != active_id)
        assert 1 <= env.cog._refresh_dashboard_for_guild.await_count <= 4

    asyncio.run(check())


def test_commands_ignored_responses_and_duplicates_do_not_create_trackers(import_app, monkeypatch):
    module = import_app("app.cogs.ghosty")

    async def check():
        env = prepare_live(module, monkeypatch)
        await env.cog.on_message(env.message(999, author_id=7))
        env.cog.service.process_message.assert_not_awaited()
        ignored = env.message(1000)
        ignored.ignored = True
        await env.cog.on_message(ignored)
        env.cog._refresh_dashboard_for_guild.assert_not_awaited()
        env.channels[2].send.assert_not_awaited()
        result = env.message(1001)
        await env.cog.on_message(result)
        await env.cog.on_message(result)
        env.channels[2].send.assert_awaited_once()
        env.cog._refresh_dashboard_for_guild.assert_awaited_once()

    asyncio.run(check())


@pytest.mark.parametrize("failure", ["send", "save"])
def test_failed_replacement_deletes_old_tracker_and_still_refreshes_dashboard(
    import_app, monkeypatch, failure
):
    module = import_app("app.cogs.ghosty")

    async def check():
        env = prepare_live(module, monkeypatch)
        await env.cog.on_message(env.message(1000))
        first = env.posts[200]
        if failure == "send":
            env.channels[2].send.side_effect = discord.Forbidden(
                SimpleNamespace(status=403, reason="Forbidden"),
                "Missing Permissions",
            )
        else:
            module.repository.set_tracker_message.side_effect = RuntimeError("database unavailable")
        await env.cog.on_message(env.message(1001))
        await drain_background(env.cog)
        first.delete.assert_awaited_once()
        assert env.states[2]["tracker_message_id"] == "200"
        assert env.cog._refresh_dashboard_for_guild.await_count == 2
        if failure == "save":
            env.posts[201].delete.assert_awaited_once()

    asyncio.run(check())


def test_hit_only_mode_and_missing_history_still_refresh_the_dashboard(import_app, monkeypatch):
    module = import_app("app.cogs.ghosty")

    async def check():
        env = prepare_live(module, monkeypatch)
        env.config["notify:2"] = "hit_only"
        await env.cog.on_message(env.message(1000))
        await drain_background(env.cog)
        env.channels[2].send.assert_not_awaited()
        env.cog._refresh_dashboard_for_guild.assert_awaited_once()
        env.config["notify:2"] = "always"
        env.config["minimum_samples"] = 75
        env.cog.service.get_live_prediction_data = MagicMock(
            return_value={
                "ready": False,
                "samples": 25,
                "minimum_samples": 75,
                "hit_at": None,
                "hit_percent": None,
            }
        )
        await env.cog.on_message(env.message(1001))
        embed = env.posts[200].embed
        assert embed.description.count("`Still learning`") == 2
        assert not embed.fields
        assert embed.footer.text is None and embed.timestamp is None
        env.cog.service.get_live_prediction_data.assert_called_once_with(
            1,
            2,
            minimum_samples=75,
            stats=env.records[1001]["stats"],
            last_gamble_at=env.records[1001]["created_at"],
        )
        assert env.cog._refresh_dashboard_for_guild.await_count == 2

    asyncio.run(check())


def test_dashboard_update_failure_does_not_prevent_tracker_delivery(import_app, monkeypatch):
    module = import_app("app.cogs.ghosty")

    async def check():
        env = prepare_live(module, monkeypatch)
        env.cog._refresh_dashboard_for_guild.side_effect = RuntimeError("dashboard unavailable")
        await env.cog.on_message(env.message(1000))
        env.channels[2].send.assert_awaited_once()
        assert env.states[2]["tracker_message_id"] == "200"

    asyncio.run(check())


def test_manually_deleted_tracker_is_replaced_normally(import_app, monkeypatch):
    module = import_app("app.cogs.ghosty")

    async def check():
        env = prepare_live(module, monkeypatch)
        await env.cog.on_message(env.message(1000))
        env.posts[200].delete.side_effect = discord.NotFound(
            SimpleNamespace(status=404, reason="Not Found"),
            "Unknown Message",
        )
        await env.cog.on_message(env.message(1001))
        await drain_background(env.cog)
        assert env.states[2]["tracker_message_id"] == "201"
        env.posts[201].delete.assert_not_awaited()
        assert env.cog._refresh_dashboard_for_guild.await_count == 2

    asyncio.run(check())


def test_hit_in_dashboard_posts_only_one_plain_text_notification(import_app, monkeypatch):
    module = import_app("app.cogs.ghosty")

    async def check():
        env = prepare_live(module, monkeypatch)
        await env.cog.on_message(env.message(1000, channel_id=5, outcome="hit", wager=2222))
        await drain_background(env.cog)
        env.channels[5].send.assert_awaited_once()
        env.channels[2].send.assert_not_awaited()
        notification = env.channels[5].send.call_args.kwargs
        assert "Kudos to <@7>" in notification["content"]
        assert "embed" not in notification and notification["suppress_embeds"]
        module.live.get_hit_reaction.assert_not_called()
        assert env.cog._refresh_dashboard_for_guild.await_count >= 1

    asyncio.run(check())
