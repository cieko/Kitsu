from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, call

import discord
import pytest

from .helpers import prepare_dashboard


@pytest.mark.parametrize(
    "static_state",
    [
        "current",
        "untracked_header",
        "legacy_controls",
    ],
)
def test_periodic_refresh_only_edits_the_live_message(import_app, monkeypatch, static_state):
    module = import_app("app.cogs.ghosty")

    async def check():
        cog, guild, channel, messages, config = prepare_dashboard(module, monkeypatch)
        if static_state == "untracked_header":
            config.pop("dashboard_tracker_message_id")
        elif static_state == "legacy_controls":
            messages[30].flags.components_v2 = False

        assert cog.refresh_dashboard.seconds == 60
        await cog.refresh_dashboard()
        await cog.refresh_dashboard()

        assert messages[20].edit.await_count == 2
        assert channel.fetch_message.await_args_list == (
            (
                [call(20), call(30)]
                if static_state == "untracked_header"
                else [call(10), call(20), call(30)]
            )
            + [call(10), call(20), call(30)]
        )
        assert all(
            item.kwargs["view"] is cog._build_dashboard_view.return_value
            for item in messages[20].edit.await_args_list
        )
        for message_id, message in messages.items():
            message.delete.assert_not_awaited()
            if message_id != 20:
                message.edit.assert_not_awaited()
        channel.send.assert_not_awaited()
        cog._build_dashboard_controls_view.assert_not_called()
        if static_state == "untracked_header":
            module.repository.set_config.assert_awaited_once_with(
                guild.id,
                "dashboard_tracker_message_id",
                "10",
            )
        else:
            module.repository.set_config.assert_not_awaited()

    asyncio.run(check())


def test_dashboard_sorts_and_displays_the_selected_candidate_probability(import_app, monkeypatch):
    module = import_app("app.cogs.ghosty")

    async def check():
        cog, guild, channel, messages, config = prepare_dashboard(module, monkeypatch)
        guild.get_channel = lambda channel_id: channel if channel_id in {5, 6} else None
        monkeypatch.setattr(
            module.repository,
            "get_all_channel_stats",
            AsyncMock(
                return_value=[
                    {"channel_id": "6", "total_gambles": 100},
                    {"channel_id": "5", "total_gambles": 100},
                ]
            ),
        )
        predictions = {
            5: {"ready": True, "hit_at": 13, "candidate_percent": 45, "hit_percent": 1},
            6: {"ready": True, "hit_at": 8, "candidate_percent": 30, "hit_percent": 90},
        }
        cog.service.get_prediction_data = AsyncMock(
            side_effect=lambda guild_id, channel_id, **kwargs: predictions[channel_id]
        )
        view = await module.GhostyCog._build_dashboard_view(cog, guild)
        links = [item.url for item in view.walk_children() if getattr(item, "url", None)]
        assert links == ["https://discord.com/channels/1/5", "https://discord.com/channels/1/6"]
        labels = [
            item.label.strip() for item in view.walk_children() if getattr(item, "label", None)
        ]
        assert "45.0%" in labels and "30.0%" in labels
        assert "1.0%" not in labels and "90.0%" not in labels

    asyncio.run(check())


@pytest.mark.parametrize("missing", ["all", "heading", "market", "controls", "first_time"])
def test_missing_stack_is_posted_once_then_only_the_middle_refreshes(
    import_app,
    monkeypatch,
    missing,
):
    module = import_app("app.cogs.ghosty")

    async def check():
        cog, guild, channel, messages, config = prepare_dashboard(module, monkeypatch)
        old_messages = dict(messages)
        if missing in {"all", "first_time"}:
            messages.clear()
            if missing == "first_time":
                config.clear()
                config["dashboard_channel_id"] = "5"
        else:
            messages.pop({"heading": 10, "market": 20, "controls": 30}[missing])
        existing_ids = set(messages)
        await cog.refresh_dashboard()
        assert channel.send.await_count == 3
        assert channel.send.await_args_list[0] == call(module.DASHBOARD_HEADING)
        for message_id, message in old_messages.items():
            assert message.delete.await_count == int(message_id in existing_ids)
        stack = [messages[int(config[key])] for key in module.DASHBOARD_MESSAGE_KEYS]
        await cog.refresh_dashboard()
        assert channel.send.await_count == 3
        stack[0].edit.assert_not_awaited()
        stack[1].edit.assert_awaited_once()
        stack[2].edit.assert_not_awaited()
        cog._build_dashboard_controls_view.assert_called_once()

    asyncio.run(check())


@pytest.mark.parametrize("reconfigure", [False, True])
def test_setup_records_all_three_messages_then_refreshes_only_the_middle(
    import_app,
    monkeypatch,
    reconfigure,
):
    module = import_app("app.cogs.ghosty")

    async def check():
        cog, guild, channel, messages, config = prepare_dashboard(module, monkeypatch)
        old_messages = list(messages.values())
        if not reconfigure:
            config.clear()
        new_messages = [
            SimpleNamespace(id=message_id, edit=AsyncMock(), delete=AsyncMock())
            for message_id in (40, 50, 60)
        ]
        messages.update({message.id: message for message in new_messages})
        channel.send.side_effect = new_messages
        interaction = SimpleNamespace(
            guild=guild,
            user=SimpleNamespace(guild_permissions=discord.Permissions(manage_guild=True)),
            response=SimpleNamespace(defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )
        await cog.setup_dashboard.callback(cog, interaction, channel)
        assert channel.send.await_count == 3
        assert channel.send.await_args_list[0] == call("## @ t r a c k e r")
        assert (
            channel.send.await_args_list[1].kwargs["view"] is cog._build_dashboard_view.return_value
        )
        assert channel.send.await_args_list[2].kwargs["view"].is_persistent()
        assert config["dashboard_tracker_message_id"] == "40"
        assert config["dashboard_message_id"] == "50"
        assert config["dashboard_controls_message_id"] == "60"
        for message in old_messages:
            assert message.delete.await_count == int(reconfigure)

        await cog.refresh_dashboard()
        await cog.refresh_dashboard()
        new_messages[0].edit.assert_not_awaited()
        assert new_messages[1].edit.await_count == 2
        new_messages[2].edit.assert_not_awaited()
        assert channel.send.await_count == 3
        cog._build_dashboard_controls_view.assert_called_once()
        for message in new_messages:
            message.delete.assert_not_awaited()

    asyncio.run(check())


def test_concurrent_initial_refreshes_post_only_one_stack(import_app, monkeypatch):
    module = import_app("app.cogs.ghosty")

    async def check():
        cog, guild, channel, messages, config = prepare_dashboard(module, monkeypatch)
        messages.clear()
        config.clear()
        config["dashboard_channel_id"] = "5"
        original_send = channel.send.side_effect

        async def delayed_send(*args, **kwargs):
            await asyncio.sleep(0)
            return original_send(*args, **kwargs)

        channel.send.side_effect = delayed_send
        assert all(
            await asyncio.gather(
                cog._refresh_dashboard_for_guild(guild),
                cog._refresh_dashboard_for_guild(guild),
            )
        )
        assert channel.send.await_count == 3
        stack = [messages[int(config[key])] for key in module.DASHBOARD_MESSAGE_KEYS]
        stack[0].edit.assert_not_awaited()
        stack[1].edit.assert_awaited_once()
        stack[2].edit.assert_not_awaited()

    asyncio.run(check())


@pytest.mark.parametrize("status", [403, 500])
def test_read_errors_do_not_trigger_duplicate_posts(import_app, monkeypatch, status):
    module = import_app("app.cogs.ghosty")

    async def check():
        cog, guild, channel, messages, config = prepare_dashboard(module, monkeypatch)
        error = discord.Forbidden if status == 403 else discord.HTTPException
        channel.fetch_message.side_effect = error(
            SimpleNamespace(status=status, reason="Request failed"),
            "Request failed",
        )
        assert not await cog._refresh_dashboard_for_guild(guild)
        channel.send.assert_not_awaited()
        for message in messages.values():
            message.edit.assert_not_awaited()
            message.delete.assert_not_awaited()
        module.repository.set_config.assert_not_awaited()

    asyncio.run(check())


@pytest.mark.parametrize("failed_send", [0, 1, 2])
def test_failed_repost_cleans_partial_messages_and_recovers_on_next_refresh(
    import_app,
    monkeypatch,
    failed_send,
):
    module = import_app("app.cogs.ghosty")

    async def check():
        cog, guild, channel, messages, config = prepare_dashboard(module, monkeypatch)
        old_messages = dict(messages)
        original_send = channel.send.side_effect
        created = []

        def fail_send(*args, **kwargs):
            if len(created) == failed_send:
                raise discord.Forbidden(
                    SimpleNamespace(status=403, reason="Forbidden"),
                    "Missing Permissions",
                )
            message = original_send(*args, **kwargs)
            created.append(message)
            return message

        channel.send.side_effect = fail_send
        assert not await cog._refresh_dashboard_for_guild(guild, repost=True)
        assert config["dashboard_channel_id"] == "5"
        # Delivered pieces are saved individually, even if a later send fails.
        for key in module.DASHBOARD_MESSAGE_KEYS[:failed_send]:
            assert int(config[key]) > 30
        for message in old_messages.values():
            message.delete.assert_awaited_once()
        for message in created:
            message.delete.assert_awaited_once()
        channel.send.side_effect = original_send
        await cog.refresh_dashboard()
        assert channel.send.await_count == failed_send + 4
        stack = [messages[int(config[key])] for key in module.DASHBOARD_MESSAGE_KEYS]
        await cog.refresh_dashboard()
        assert channel.send.await_count == failed_send + 4
        stack[0].edit.assert_not_awaited()
        stack[1].edit.assert_awaited_once()
        stack[2].edit.assert_not_awaited()

    asyncio.run(check())
