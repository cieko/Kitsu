from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from .helpers import drain_background, prepare_live


@pytest.mark.parametrize(
    "streak_length, expected", [(6, [11, 12, 13]), (2, [12, 13]), (0, []), (99, [11, 12, 13])]
)
def test_contributor_pings_respect_the_completed_streak_and_manual_resets(
    import_app, monkeypatch, streak_length, expected
):
    module = import_app("app.cogs.ghosty")
    history = [
        {"result_message_id": str(message_id), "user_id": str(user_id), "outcome": outcome}
        for message_id, user_id, outcome in (
            (10, 99, "miss"),
            (20, 98, "hit"),
            (30, 11, "miss"),
            (31, 11, "miss"),
            (32, 0, "miss"),
            (33, 7, "miss"),
            (34, 12, "miss"),
            (35, 13, "miss"),
            (40, 7, "hit"),
            (41, 44, "miss"),
            (42, 50, "hit"),
        )
    ]
    query = AsyncMock(return_value=history)
    monkeypatch.setattr(module.repository, "get_gamble_history", query)

    async def check():
        env = prepare_live(module, monkeypatch)
        participants = await module.GhostyCog._get_streak_participant_ids(
            env.cog,
            1,
            2,
            40,
            7,
            streak_length=streak_length,
        )
        assert participants == expected
        if streak_length:
            query.assert_awaited_once_with(1, 2, limit=None)
        else:
            query.assert_not_awaited()

    asyncio.run(check())


@pytest.mark.parametrize("contributors, profit", [([11, 11, 7, 0, 12], 1389), ([], -1389)])
def test_market_notification_deduplicates_pings_and_preserves_signed_profit(
    import_app, monkeypatch, contributors, profit
):
    module = import_app("app.cogs.ghosty")

    async def check():
        env = prepare_live(module, monkeypatch)
        env.states[2].update(streak=16, streak_loss=22220 - profit)
        env.cog._get_streak_participant_ids.return_value = contributors
        await env.cog.on_message(env.message(1000, outcome="hit", wager=2222))
        await drain_background(env.cog)
        sent = env.channels[5].send.call_args.kwargs
        assert sent["content"].splitlines()[0] == ("<#2> <@11> <@12>" if contributors else "<#2>")
        assert sent["content"].count("<@7>") == 1
        assert f"Total streak: 16 ・ Total profit: {profit:,} Goos" in sent["content"]
        assert {user.id for user in sent["allowed_mentions"].users} == (
            {7, 11, 12} if contributors else {7}
        )
        env.cog._get_streak_participant_ids.assert_awaited_once_with(
            1, 2, 1000, 7, streak_length=16
        )
        assert "embed" not in sent
        assert isinstance(env.channels[2].send.call_args.kwargs["embed"], discord.Embed)

    asyncio.run(check())


def test_hit_sends_market_notification_and_complete_source_summary_without_an_ai_service(
    import_app, monkeypatch
):
    module = import_app("app.cogs.ghosty")
    ai = import_app("app.services.ai_service")
    create_ai = MagicMock(side_effect=AssertionError("Hit summaries must not start an LLM"))
    monkeypatch.setattr(ai.AIService, "from_settings", create_ai)

    async def check():
        env = prepare_live(module, monkeypatch)
        await env.cog.on_message(env.message(1000, outcome="hit"))
        await drain_background(env.cog)
        env.channels[5].send.assert_awaited_once()
        assert "Kudos to <@7>" in env.channels[5].send.call_args.kwargs["content"]
        assert env.cog._refresh_dashboard_for_guild.await_count >= 1
        summary = env.channels[2].send.call_args.kwargs["embed"]
        assert "Ghosty says you're welcome" in summary.description
        create_ai.assert_not_called()

    asyncio.run(check())


def test_slow_dashboard_cannot_delay_trackers_and_bursts_refresh_the_latest_state(
    import_app, monkeypatch
):
    module = import_app("app.cogs.ghosty")

    async def check():
        env = prepare_live(module, monkeypatch)
        entered, release = asyncio.Event(), asyncio.Event()
        snapshots = []

        async def slow_dashboard(guild):
            snapshots.append(env.states[2]["streak"])
            entered.set()
            await release.wait()

        env.cog._refresh_dashboard_for_guild.side_effect = slow_dashboard
        try:
            await asyncio.wait_for(env.cog.on_message(env.message(1000)), timeout=0.5)
            await asyncio.wait_for(entered.wait(), timeout=0.5)
            for number in range(1001, 1005):
                await asyncio.wait_for(env.cog.on_message(env.message(number)), timeout=0.5)
            assert env.channels[2].send.await_count == 5
            assert env.cog._refresh_dashboard_for_guild.await_count == 1
            assert len(env.cog._dashboard_refresh_tasks) == 1
            release.set()
            await asyncio.wait_for(drain_background(env.cog), timeout=1)
            assert env.cog._refresh_dashboard_for_guild.await_count == 2
            assert snapshots[-1] == 7
        finally:
            release.set()
            await drain_background(env.cog)

    asyncio.run(check())


def test_hit_reaction_is_complete_immediately_and_only_the_estimate_is_edited(
    import_app, monkeypatch
):
    module = import_app("app.cogs.ghosty")

    async def check():
        env = prepare_live(module, monkeypatch)
        await asyncio.wait_for(env.cog.on_message(env.message(1000, outcome="hit")), timeout=0.5)
        await asyncio.wait_for(drain_background(env.cog), timeout=0.5)
        env.channels[5].send.assert_awaited_once()
        env.channels[2].send.assert_awaited_once()
        summary = next(post for post in env.posts.values() if post.embed is not None)
        initial = env.channels[2].send.call_args.kwargs["embed"]
        loading_line = "<a:loading:1564713766220865617> Learning from this hit…"
        assert initial.description.endswith(loading_line)
        assert "Next Estimated Hit" not in initial.description
        assert summary.embed.description.split("**Next Estimated Hit @**")[
            0
        ] == initial.description.removesuffix(loading_line)
        assert "Ghosty says you're welcome" in summary.embed.description
        assert (
            "loading" not in summary.embed.description
            and "Preparing a reaction" not in summary.embed.description
        )
        assert summary.embed.description.endswith("**Next Estimated Hit @** `10`")
        summary.edit.assert_awaited_once()
        module.live.get_application_emoji.assert_awaited_once_with(env.cog.bot, "loading", "⏳")
        env.cog.service.get_prediction_data.assert_awaited_once_with(
            1, 2, minimum_samples=50, refresh_model=True
        )
        await asyncio.wait_for(env.cog.on_message(env.message(1001, wager=400)), timeout=0.5)
        await drain_background(env.cog)
        assert env.states[2]["streak"] == 1 and env.states[2]["streak_loss"] == 400
        summary.delete.assert_not_awaited()
        summary.edit.assert_awaited_once()
        env.channels[5].send.assert_awaited_once()

    asyncio.run(check())


def test_learning_cannot_delay_the_hit_reaction_or_subsequent_gambles(import_app, monkeypatch):
    module = import_app("app.cogs.ghosty")

    async def check():
        env = prepare_live(module, monkeypatch)
        entered, release = asyncio.Event(), asyncio.Event()

        async def learn(*args, **kwargs):
            assert env.channels[2].send.await_count == 1
            entered.set()
            await release.wait()
            return {"ready": True, "hit_at": 13}

        env.cog.service.get_prediction_data.side_effect = learn
        try:
            await asyncio.wait_for(
                env.cog.on_message(env.message(1000, outcome="hit")), timeout=0.5
            )
            await asyncio.wait_for(entered.wait(), timeout=0.5)
            summary = next(post for post in env.posts.values() if post.embed is not None)
            assert "Ghosty says you're welcome" in summary.embed.description
            assert "<a:loading:1564713766220865617>" in summary.embed.description
            summary.edit.assert_not_awaited()
            await asyncio.wait_for(env.cog.on_message(env.message(1001, wager=400)), timeout=0.5)
            assert env.states[2]["streak"] == 1
            release.set()
            await asyncio.wait_for(drain_background(env.cog), timeout=0.5)
            assert summary.embed.description.endswith("**Next Estimated Hit @** `13`")
            summary.edit.assert_awaited_once()
            module.live.get_hit_reaction.assert_called_once()
            summary.delete.assert_not_awaited()
        finally:
            release.set()
            await drain_background(env.cog)

    asyncio.run(check())


@pytest.mark.parametrize(
    "case, expected",
    [
        ("error", "Unavailable"),
        ("not_ready", "Still learning"),
        ("later_streak", "Streak already ended"),
        ("later_streak_not_ready", "Streak already ended"),
    ],
)
def test_estimate_loading_resolves_without_changing_the_reaction(
    import_app, monkeypatch, case, expected
):
    module = import_app("app.cogs.ghosty")

    async def check():
        env = prepare_live(module, monkeypatch)
        if case == "error":
            env.cog.service.get_prediction_data.side_effect = RuntimeError("training unavailable")
        elif case == "not_ready":
            env.cog.service.get_prediction_data.return_value = {"ready": False, "hit_at": None}
        elif case == "later_streak_not_ready":
            env.cog.service.get_prediction_data.return_value = {"ready": False, "streak_base": 999}
        else:
            env.cog.service.get_prediction_data.return_value = {
                "ready": True,
                "hit_at": 22,
                "streak_forecast": {"streak_base": 999},
            }
        await env.cog.on_message(env.message(1000, outcome="hit"))
        await drain_background(env.cog)
        summary = next(post for post in env.posts.values() if post.embed is not None)
        assert summary.embed.description.endswith(f"**Next Estimated Hit @** `{expected}`")
        assert "Ghosty says you're welcome" in summary.embed.description
        assert "loading" not in summary.embed.description
        summary.edit.assert_awaited_once()
        assert summary.edit.call_args.kwargs["allowed_mentions"].users is False
        env.channels[2].send.assert_awaited_once()

    asyncio.run(check())


def test_deleted_hit_summary_is_not_recreated_when_learning_finishes(import_app, monkeypatch):
    module = import_app("app.cogs.ghosty")

    async def check():
        env = prepare_live(module, monkeypatch)

        async def learn(*args, **kwargs):
            summary = next(post for post in env.posts.values() if post.embed is not None)
            summary.edit.side_effect = discord.NotFound(
                SimpleNamespace(status=404, reason="Not Found"),
                "Unknown Message",
            )
            return {"ready": True, "hit_at": 13}

        env.cog.service.get_prediction_data.side_effect = learn
        await env.cog.on_message(env.message(1000, outcome="hit"))
        await drain_background(env.cog)
        env.channels[2].send.assert_awaited_once()

    asyncio.run(check())
