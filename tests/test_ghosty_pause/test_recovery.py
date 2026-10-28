from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord

from .helpers import prepare


def test_legacy_results_require_actor_proof_and_stop_at_the_last_hit(import_app, monkeypatch):
    module = import_app("app.services.ghosty_pause_service")

    async def check():
        env = prepare(module, monkeypatch)
        for row in env.rows:
            row.pop("attribution_verified")
        wrong_actor = {"value": False}

        async def fetch_message(channel, result_id):
            return SimpleNamespace(
                guild=env.guild,
                channel=channel,
                author=env.members[module.GHOSTY_BOT_ID],
                id=result_id,
                reference=None,
                content=f"<@{101 if wrong_actor['value'] else 100}>, you've successfully sent 100 goos",
            )

        monkeypatch.setattr(discord.TextChannel, "fetch_message", fetch_message)
        assert await env.service.eligible_count(env.fresh(), env.members[100]) == 5
        wrong_actor["value"] = True
        assert await env.service.eligible_count(env.fresh(), env.members[100]) == 0
        wrong_actor["value"] = False
        env.rows[2]["outcome"] = "hit"
        assert await env.service.eligible_count(env.fresh(), env.members[100]) == 2

    asyncio.run(check())


def test_live_attribution_does_not_grant_a_nearby_gambler_someone_elses_results(
    import_app, monkeypatch
):
    module = import_app("app.services.ghosty_pause_service")

    async def check():
        env = prepare(module, monkeypatch)
        channel = env.fresh()
        stamp = datetime.now(timezone.utc)
        source = SimpleNamespace(
            guild=env.guild,
            channel=channel,
            author=env.members[101],
            id=1,
            content="?give ghosty 100",
            created_at=stamp,
        )
        result = SimpleNamespace(
            guild=env.guild,
            channel=channel,
            author=env.members[module.GHOSTY_BOT_ID],
            id=2,
            content="<@100>, you've successfully sent 100 goos",
            reference=None,
            created_at=stamp,
        )
        analyzed = await env.service.tracker.analyze_result_message(result, source)
        assert analyzed["user_id"] == 100 and analyzed["attribution_verified"] is True
        result.content = (
            "<:goos:1234> | **player100 <:badge:12345>**, you've successfully sent 100 goos"
        )
        analyzed = await env.service.tracker.analyze_result_message(result, source)
        assert analyzed["user_id"] == 100 and analyzed["attribution_verified"] is True
        source.author = env.members[100]
        analyzed = await env.service.tracker.analyze_result_message(result, source)
        assert analyzed["user_id"] == 100 and analyzed["attribution_verified"] is True
        result.content = (
            "<:goos:1234> | **\u200eplayer100\u200e <:badge:12345>**, you really want to send **100** goos to me? "
            "You're going to get **1,000** goos!"
        )
        source.author = env.members[101]
        analyzed = await env.service.tracker.analyze_result_message(result, source)
        assert (
            analyzed["outcome"] == "hit"
            and analyzed["user_id"] == 100
            and analyzed["attribution_verified"] is True
        )
        analyzed = await env.service.tracker.analyze_result_message(result)
        assert analyzed["user_id"] == 100 and analyzed["attribution_verified"] is True

    asyncio.run(check())


def test_pause_repository_writes_only_existing_channel_pause_fields_and_scopes_history(
    import_app, monkeypatch
):
    module = import_app("app.database.repositories.ghosty_pause_repository")
    stats, history = MagicMock(), MagicMock()
    stats.update_one.return_value = SimpleNamespace(modified_count=1, matched_count=1)
    cursor = history.find.return_value.sort.return_value.limit.return_value
    cursor.__iter__.return_value = iter([{"user_id": "100"}])
    monkeypatch.setattr(module.tracking, "channel_stats", stats)
    monkeypatch.setattr(module.tracking, "gamble_history", history)
    stamp = datetime.now(timezone.utc)

    async def check():
        record = {"id": "pause-id", "revision": 1}
        assert await module.create_pause(1, 50, record)
        assert await module.save_pause(1, 50, record, revision=0)
        assert await module.clear_pause(1, 50, "pause-id", revision=1)
        assert await module.get_current_streak(1, 50, streak=0, now=stamp) == []
        assert await module.get_current_streak(1, 50, streak=7, now=stamp) == [{"user_id": "100"}]

    asyncio.run(check())
    for call in stats.update_one.call_args_list:
        query, update = call.args
        assert query["guild_id"] == "1" and query["channel_id"] == "50"
        assert not call.kwargs.get("upsert")
        assert set(next(iter(update.values()))) == {"pause"}
    assert stats.update_one.call_args_list[1].args[0]["pause.revision"] == 0
    assert stats.update_one.call_args_list[2].args[0]["pause.id"] == "pause-id"
    query = history.find.call_args.args[0]
    assert query == {
        "guild_id": "1",
        "channel_id": "50",
        "created_at": {"$lte": stamp},
        "outcome": {"$in": ["hit", "miss"]},
    }
    history.find.return_value.sort.return_value.limit.assert_called_once_with(7)
    stats.insert_one.assert_not_called()
    history.update_one.assert_not_called()


def test_duration_dispatch_rechecks_eligibility_and_rejects_other_requesters(
    import_app, monkeypatch
):
    module = import_app("app.services.ghosty_pause_service")
    ui = import_app("app.services.ghosty_pause_ui")

    async def check():
        env = prepare(module, monkeypatch)
        cog = SimpleNamespace(bot=env.bot, pause_service=env.service)
        view = ui.PauseSetupView(cog, env.members[100], env.fresh(), count=5)
        view.on_error = AsyncMock()
        select = next(item for item in view.walk_children() if isinstance(item, discord.ui.Select))

        def interaction(user_id):
            return SimpleNamespace(
                user=env.members[user_id],
                guild=env.guild,
                channel=env.fresh(),
                channel_id=50,
                message=SimpleNamespace(id=123),
                data={"values": ["5m"]},
                response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock()),
                followup=SimpleNamespace(send=AsyncMock()),
            )

        stranger = interaction(101)
        await view._scheduled_task(select, stranger)
        stranger.response.send_message.assert_awaited_once()
        stranger.response.defer.assert_not_awaited()
        env.state["stats"]["streak"] = 0
        owner = interaction(100)
        await view._scheduled_task(select, owner)
        owner.followup.send.assert_awaited_once()
        assert "verify 0" in owner.followup.send.call_args.args[0]
        assert not env.state["edits"]
        env.state["stats"]["streak"] = 7
        await view._scheduled_task(select, interaction(100))
        assert env.state["pause"]["owner_id"] == "100"
        view.on_error.assert_not_awaited()

    asyncio.run(check())


def test_prefix_pause_and_resume_are_public_and_use_the_channel_context(import_app, monkeypatch):
    module = import_app("app.services.ghosty_pause_service")
    cog_module = import_app("app.cogs.ghosty")

    async def check():
        env = prepare(module, monkeypatch)
        cog = cog_module.GhostyCog(env.bot)
        cog.pause_service = env.service
        ctx = SimpleNamespace(
            guild=env.guild,
            channel=env.fresh(),
            author=env.members[100],
            send=AsyncMock(),
            clean_prefix="saved-prefix ",
        )
        await cog.ghosty_pause_text.callback(cog, ctx)
        assert isinstance(ctx.send.call_args.kwargs["view"], cog_module.PauseSetupView)
        for name in ("pause", "resume"):
            assert cog.ghosty_text.get_command(name) is not None
            assert not cog.ghosty_text.get_command(name).checks
        await env.service.reserve(env.fresh(), env.members[100], "5m", panel_id=123)
        await cog.ghosty_resume_text.callback(cog, ctx)
        assert env.state["pause"] is None
        assert "Channel resumed" in ctx.send.call_args.args[0]

    asyncio.run(check())
