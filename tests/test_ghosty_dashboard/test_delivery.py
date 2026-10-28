from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from .helpers import add_market_message, finish_background, prepare_dashboard


@pytest.mark.parametrize("permissions", [{}, {"manage_messages": True}, {"administrator": True}])
def test_market_text_moves_stack_below_message_then_resumes_middle_only_refresh(
    import_app,
    monkeypatch,
    permissions,
):
    module = import_app("app.cogs.ghosty")

    async def check():
        cog, guild, channel, messages, config = prepare_dashboard(module, monkeypatch)
        incoming = add_market_message(guild, channel, messages, **permissions)
        await cog.on_message(incoming)
        await finish_background(cog)

        assert channel.operations[:3] == [("delete", 10), ("delete", 20), ("delete", 30)]
        assert channel.operations[3:] == [("send", 45), ("send", 55), ("send", 65)]
        stack = [messages[int(config[key])] for key in module.DASHBOARD_MESSAGE_KEYS]
        assert stack[0].id > incoming.id
        assert not cog._dashboard_repost_after
        assert incoming.id in messages
        incoming.delete.assert_not_awaited()

        # The bot's new stack messages must not trigger another move.
        for message in stack:
            await cog.on_message(message)
        await cog.refresh_dashboard()
        await cog.refresh_dashboard()
        await finish_background(cog)
        assert channel.send.await_count == 3
        stack[0].edit.assert_not_awaited()
        assert stack[1].edit.await_count == 2
        stack[2].edit.assert_not_awaited()

    asyncio.run(check())


def test_market_message_burst_and_periodic_refresh_share_one_repost(import_app, monkeypatch):
    module = import_app("app.cogs.ghosty")

    async def check():
        cog, guild, channel, messages, config = prepare_dashboard(module, monkeypatch)
        incoming = [
            add_market_message(guild, channel, messages, message_id=message_id)
            for message_id in (35, 36, 37)
        ]
        await asyncio.gather(
            *(cog.on_message(message) for message in incoming), cog.refresh_dashboard()
        )
        await finish_background(cog)
        assert channel.send.await_count == 3
        assert int(config["dashboard_tracker_message_id"]) > incoming[-1].id
        assert not cog._dashboard_repost_after
        for message in incoming:
            assert message.id in messages
            message.delete.assert_not_awaited()
        await cog.refresh_dashboard()
        assert channel.send.await_count == 3

    asyncio.run(check())


def test_message_arriving_inside_repost_moves_complete_stack_below_it(import_app, monkeypatch):
    module = import_app("app.cogs.ghosty")

    async def check():
        cog, guild, channel, messages, config = prepare_dashboard(module, monkeypatch)
        incoming = add_market_message(guild, channel, messages)
        original_send = channel.send.side_effect
        interrupted = None

        async def send(*args, **kwargs):
            nonlocal interrupted
            message = original_send(*args, **kwargs)
            if interrupted is None and args == (module.DASHBOARD_HEADING,):
                interrupted = add_market_message(
                    guild, channel, messages, message_id=message.id + 1
                )
                await cog.on_message(interrupted)
            return message

        channel.send.side_effect = send
        await cog.on_message(incoming)
        await finish_background(cog)
        assert channel.send.await_count == 6
        assert int(config["dashboard_tracker_message_id"]) > interrupted.id
        final_ids = [int(config[key]) for key in module.DASHBOARD_MESSAGE_KEYS]
        assert set(messages) == {incoming.id, interrupted.id, *final_ids}
        assert not cog._dashboard_repost_after
        incoming.delete.assert_not_awaited()
        interrupted.delete.assert_not_awaited()
        await cog.refresh_dashboard()
        assert channel.send.await_count == 6
        messages[final_ids[1]].edit.assert_awaited_once()

    asyncio.run(check())


def test_plaintext_hit_notification_is_preserved_above_reposted_stack(import_app, monkeypatch):
    module = import_app("app.cogs.ghosty")

    async def check():
        cog, guild, channel, messages, config = prepare_dashboard(module, monkeypatch)
        hit = add_market_message(guild, channel, messages, author_id=module.GHOSTY_BOT_ID)
        cog._get_streak_participant_ids = AsyncMock(return_value=[11, 12])
        cog.service.get_prediction_data = AsyncMock(return_value={"ready": False})
        recorded = {
            "user_id": 7,
            "result_message_id": hit.id,
            "stats": {"streak_net_pl": 1389, "last_streak": 16},
        }
        await cog._announce_hit(hit, recorded)
        await finish_background(cog)

        notification = messages[45]
        assert notification.content == (
            "<#5> <@11> <@12>\n"
            "🎉  **A Ghosty hit has been recorded!** Kudos to <@7>\n"
            "Total streak: 16 ・ Total profit: 1,389 Goos\n"
            f"-# [Jump to message]({hit.jump_url})"
        )
        assert "embed" not in channel.send.await_args_list[0].kwargs
        assert "view" not in channel.send.await_args_list[0].kwargs
        assert channel.operations == [
            ("send", 45),
            ("delete", 10),
            ("delete", 20),
            ("delete", 30),
            ("send", 55),
            ("send", 65),
            ("send", 75),
        ]
        assert not cog._dashboard_repost_after
        notification.delete.assert_not_awaited()
        await cog.on_message(notification)
        live = messages[int(config["dashboard_message_id"])]
        previous_edits = live.edit.await_count
        await cog.refresh_dashboard()
        assert channel.send.await_count == 4
        assert live.edit.await_count == previous_edits + 1
        messages[int(config["dashboard_tracker_message_id"])].edit.assert_not_awaited()
        messages[int(config["dashboard_controls_message_id"])].edit.assert_not_awaited()

    asyncio.run(check())


def test_delete_failure_preserves_repost_request_and_recovers_on_periodic_refresh(
    import_app, monkeypatch
):
    module = import_app("app.cogs.ghosty")

    async def check():
        cog, guild, channel, messages, config = prepare_dashboard(module, monkeypatch)
        incoming = add_market_message(guild, channel, messages)
        market = messages[20]
        original_delete = market.delete.side_effect
        market.delete.side_effect = discord.Forbidden(
            SimpleNamespace(status=403, reason="Forbidden"),
            "Missing Permissions",
        )
        await cog.on_message(incoming)
        await finish_background(cog)
        channel.send.assert_not_awaited()
        assert cog._dashboard_repost_after[guild.id] == incoming.id
        assert set(messages) == {incoming.id, market.id}

        market.delete.side_effect = original_delete
        await cog.refresh_dashboard()
        assert channel.send.await_count == 3
        assert not cog._dashboard_repost_after
        incoming.delete.assert_not_awaited()
        stack = [messages[int(config[key])] for key in module.DASHBOARD_MESSAGE_KEYS]
        await cog.refresh_dashboard()
        assert channel.send.await_count == 3
        stack[1].edit.assert_awaited_once()

    asyncio.run(check())


def test_repost_prepares_views_before_deleting_existing_stack(import_app, monkeypatch):
    module = import_app("app.cogs.ghosty")

    async def check():
        cog, guild, channel, messages, config = prepare_dashboard(module, monkeypatch)
        incoming = add_market_message(guild, channel, messages)
        cog._build_dashboard_view.side_effect = RuntimeError("temporary database failure")
        await cog.on_message(incoming)
        await finish_background(cog)
        assert channel.operations == []
        assert set(messages) == {10, 20, 30, incoming.id}
        assert cog._dashboard_repost_after[guild.id] == incoming.id
        cog._build_dashboard_view.side_effect = None
        await cog.refresh_dashboard()
        assert channel.send.await_count == 3
        assert not cog._dashboard_repost_after

    asyncio.run(check())


@pytest.mark.parametrize("outcome", ["miss", "hit"])
def test_confirmed_results_in_another_channel_refresh_only_the_live_dashboard(
    import_app,
    monkeypatch,
    outcome,
):
    module = import_app("app.cogs.ghosty")

    async def check():
        cog, guild, channel, messages, config = prepare_dashboard(module, monkeypatch)
        source = SimpleNamespace(id=6)
        result = SimpleNamespace(
            id=100, guild=guild, channel=source, author=SimpleNamespace(id=module.GHOSTY_BOT_ID)
        )
        cog.service.process_message = AsyncMock(return_value={"outcome": outcome})
        # Delivery failures must not leave the successfully recorded dashboard stale.
        cog._send_miss_tracker = AsyncMock(side_effect=RuntimeError("cannot send tracker"))
        cog._announce_hit = AsyncMock(side_effect=RuntimeError("cannot send celebration"))
        await cog.on_message(result)
        await asyncio.gather(*tuple(cog._background_tasks), return_exceptions=True)
        messages[20].edit.assert_awaited_once()
        messages[10].edit.assert_not_awaited()
        messages[30].edit.assert_not_awaited()
        channel.send.assert_not_awaited()
        module.repository.set_config.assert_not_awaited()

    asyncio.run(check())


@pytest.mark.parametrize("saved_page", [0, 4])
def test_dashboard_uses_real_channel_data_and_only_saves_a_changed_page(
    import_app,
    monkeypatch,
    saved_page,
):
    module = import_app("app.cogs.ghosty")

    async def check():
        cog, guild, channel, messages, config = prepare_dashboard(module, monkeypatch)
        config["dashboard_page"] = saved_page
        config["dashboard_excluded_channels"] = [7]
        documents = [
            {"channel_id": "5", "total_gambles": 100, "streak": 2, "streak_loss": 20},
            {"channel_id": "6", "total_gambles": 0},
            {"channel_id": "7", "total_gambles": 100},
            {"channel_id": "99", "total_gambles": 100},
        ]
        guild.get_channel = lambda channel_id: channel if channel_id in {5, 6, 7} else None
        monkeypatch.setattr(
            module.repository, "get_all_channel_stats", AsyncMock(return_value=documents)
        )
        cog.service.get_prediction_data = AsyncMock(
            return_value={
                "ready": True,
                "hit_percent": 10,
                "hit_at": 12,
            }
        )
        for _ in range(2):
            view = await module.GhostyCog._build_dashboard_view(cog, guild)
            links = [item.url for item in view.walk_children() if getattr(item, "url", None)]
            assert links == ["https://discord.com/channels/1/5"]
        assert cog.service.get_prediction_data.await_count == 2
        assert all(call.args == (1, 5) for call in cog.service.get_prediction_data.await_args_list)
        assert module.repository.set_config.await_count == int(saved_page != 0)
        assert config["dashboard_page"] == 0

    asyncio.run(check())
