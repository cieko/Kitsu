from __future__ import annotations

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import MagicMock

import discord
import pytest
from pymongo.errors import AutoReconnect

from .helpers import environment, interaction


@pytest.mark.parametrize("command", ["add", "edit", "toggle"])
def test_management_commands_post_immediately_and_keep_ignore_bots(
    import_app, monkeypatch, command
):
    module = import_app("app.cogs.sticky")

    async def check():
        env = environment(module, monkeypatch)
        current = interaction(env)
        callback = getattr(env.cog, command).callback
        await callback(env.cog, current, env.channel, True if command == "toggle" else "New sticky")
        current.response.defer.assert_awaited_once_with(ephemeral=True)
        current.followup.send.assert_awaited_once()
        assert env.channel.send.await_count == int(command != "toggle")
        assert env.state["row"]["last_message_id"] == (50 if command == "toggle" else 1001)
        assert (50 in env.state["deleted"]) is (command != "toggle")
        assert env.state["row"]["ignore_bots"] is False
        assert not env.state["row"].get("pending_delete_id")

    asyncio.run(check())


@pytest.mark.parametrize("case", ["user", "bot", "ignore_bot", "self", "paused", "other_guild"])
def test_listener_respects_triggers_and_never_reposts_itself(import_app, monkeypatch, case):
    module = import_app("app.cogs.sticky")

    async def check():
        env = environment(module, monkeypatch)
        author = SimpleNamespace(id=8, bot=case in {"bot", "ignore_bot", "self"})
        if case == "self":
            author = env.bot.user
        env.state["row"]["ignore_bots"] = case == "ignore_bot"
        env.state["row"]["active"] = case != "paused"
        event = env.message(60, author=author)
        if case == "other_guild":
            event.guild = SimpleNamespace(id=99)
        await env.cog.on_message(event)
        assert env.channel.send.await_count == int(case in {"user", "bot"})
        if case in {"user", "bot"}:
            await env.cog.on_message(env.state["messages"][1001])
            assert env.channel.send.await_count == 1

    asyncio.run(check())


@pytest.mark.parametrize("failure", ["send", "save", "invalid", "permissions"])
def test_failed_repost_keeps_at_most_one_copy_and_recovers(import_app, monkeypatch, failure):
    module = import_app("app.cogs.sticky")

    async def check():
        env = environment(module, monkeypatch)
        if failure == "send":
            env.state["send_error"] = discord.Forbidden(
                SimpleNamespace(status=403, reason="Forbidden"), "Cannot send"
            )
        elif failure == "save":
            env.state["save_error"] = AutoReconnect("database unavailable")
        elif failure == "invalid":
            env.state["row"]["message_script"] = "{embed}"
        else:
            env.permissions.send_messages = False
        await env.cog.on_message(env.message(60, author=SimpleNamespace(id=8, bot=False)))
        assert env.state["row"]["last_message_id"] == 50
        assert (50 in env.state["messages"]) is (failure in {"invalid", "permissions"})
        assert (1001 in env.state["messages"]) is (failure == "save")
        env.state.update(send_error=None, save_error=None)
        env.state["row"]["message_script"] = "Old sticky"
        env.permissions.send_messages = True
        restarted = module.Sticky(env.bot)
        await restarted.reconcile_stickies()
        assert sum(message.author.id == 900 for message in env.state["messages"].values()) == 1

    asyncio.run(check())


def test_pending_cleanup_survives_restart_and_retries_without_another_repost(
    import_app, monkeypatch
):
    module = import_app("app.cogs.sticky")

    async def check():
        env = environment(module, monkeypatch)
        env.state["delete_error"] = 50
        await env.cog.on_message(env.message(60, author=SimpleNamespace(id=8, bot=False)))
        assert env.state["row"]["last_message_id"] == 50
        env.channel.send.assert_not_awaited()
        assert 50 in env.state["messages"]
        env.state["delete_error"] = None
        restarted = module.Sticky(env.bot)
        await restarted.reconcile_stickies()
        assert env.channel.send.await_count == 1
        assert not env.state["row"].get("pending_delete_id")
        assert 50 not in env.state["messages"]

    asyncio.run(check())


@pytest.mark.parametrize(
    "case",
    [
        "missed_user",
        "missed_bot",
        "deleted",
        "missing_pointer",
        "quiet",
        "own_message",
        "ignored_bot",
        "paused",
    ],
)
def test_worker_recovers_missed_or_deleted_messages_without_refreshing_quiet_stickies(
    import_app, monkeypatch, case
):
    module = import_app("app.cogs.sticky")

    async def check():
        env = environment(module, monkeypatch)
        if case == "deleted":
            del env.state["messages"][50]
        elif case == "missing_pointer":
            del env.state["row"]["last_message_id"]
            del env.state["messages"][50]
        elif case != "quiet":
            author = (
                env.bot.user
                if case == "own_message"
                else SimpleNamespace(id=8, bot=case in {"missed_bot", "ignored_bot"})
            )
            env.message(60, author=author)
            env.state["row"]["ignore_bots"] = case == "ignored_bot"
            env.state["row"]["active"] = case != "paused"
        await env.cog.reconcile_stickies()
        expected = int(case in {"missed_user", "missed_bot", "deleted", "missing_pointer"})
        assert env.channel.send.await_count == expected
        await env.cog.reconcile_stickies()
        assert env.channel.send.await_count == expected

    asyncio.run(check())


def test_burst_events_coalesce_and_removal_cannot_leave_an_untracked_repost(
    import_app, monkeypatch
):
    module = import_app("app.cogs.sticky")

    async def check():
        env = environment(module, monkeypatch)
        events = [
            env.message(value, author=SimpleNamespace(id=8, bot=False)) for value in (60, 61, 62)
        ]
        await asyncio.gather(*(env.cog.on_message(event) for event in events))
        assert env.channel.send.await_count == 1
        event = env.message(1002, author=SimpleNamespace(id=8, bot=False))
        env.state["next_id"] = 1002
        await asyncio.gather(
            env.cog.on_message(event), env.cog.remove_sticky(env.guild, 2, env.channel)
        )
        assert env.state["row"] is None
        assert all(message.author.id != 900 for message in env.state["messages"].values())

    asyncio.run(check())


def test_modal_keeps_original_target_and_rechecks_administrator_permission(import_app, monkeypatch):
    module = import_app("app.cogs.sticky")

    async def check():
        env = environment(module, monkeypatch)
        view = module.StickyListView(env.guild, [deepcopy(env.state["row"])], cog=env.cog)
        view.selected_channel_id = 2
        modal = module.StickyEditModal(view, "Old sticky")
        modal.message_input._value = "Edited in modal"
        view.selected_channel_id = 99
        unauthorized = interaction(env, admin=False)
        await modal.on_submit(unauthorized)
        unauthorized.response.send_message.assert_awaited_once()
        module.repository.set_sticky.assert_not_awaited()
        current = interaction(env)
        await modal.on_submit(current)
        assert env.state["row"]["message_script"] == "Edited in modal"
        assert env.state["row"]["channel_id"] == 2
        assert view.selected_channel_id == 2
        current.edit_original_response.assert_awaited_once_with(view=view)
        assert env.channel.send.await_count == 1

    asyncio.run(check())


def test_large_sticky_lists_paginate_within_component_limits(import_app, monkeypatch):
    module = import_app("app.cogs.sticky")

    async def check():
        env = environment(module, monkeypatch)
        rows = [{**env.state["row"], "channel_id": channel_id} for channel_id in range(30)]
        view = module.StickyListView(env.guild, rows, cog=env.cog)
        for page in range(3):
            assert view.page == page and view.total_children_count <= 40
            menu = next(
                item for item in view.walk_children() if isinstance(item, discord.ui.Select)
            )
            assert [int(option.value) for option in menu.options] == list(
                range(page * 10, (page + 1) * 10)
            )
            assert (
                sum(
                    len(item.content)
                    for item in view.walk_children()
                    if isinstance(item, discord.ui.TextDisplay)
                )
                < 4000
            )
            if page < 2:
                await view.turn_page(interaction(env), 1)

    asyncio.run(check())


def test_renderer_resolves_guild_placeholders_and_rejects_discord_invalid_embeds(import_app):
    module = import_app("app.services.sticky_service")
    service = module.StickyService()
    guild = SimpleNamespace(
        name="Market", icon=SimpleNamespace(url="https://example.com/icon.webp")
    )
    content, embed = service.render(
        "{content: Welcome to {guild.name}} {embed} {title: Rules} {description: Read these} {footer: {guild.name} {guild.icon}}",
        guild,
    )
    assert content == "Welcome to Market"
    assert embed.title == "Rules" and embed.description == "Read these"
    assert embed.footer.text == "Market" and embed.footer.icon_url == guild.icon.url
    for script in (
        "{embed}",
        "{color: #73052B}",
        "{description: " + "x" * 4096 + "}{footer: " + "y" * 2048 + "}",
    ):
        with pytest.raises(ValueError):
            service.render(script, guild)


def test_pointer_update_saves_durable_cleanup_without_creating_configurations(
    import_app, monkeypatch
):
    repository = import_app("app.database.repositories.sticky_repository")
    collection = MagicMock()
    collection.update_one.return_value.matched_count = 1
    monkeypatch.setattr(repository, "sticky_collection", collection)
    assert asyncio.run(repository.update_sticky_message_id(1, 2, 1001, pending_delete_id=50))
    collection.update_one.assert_called_once_with(
        {"guild_id": 1, "channel_id": 2},
        {"$set": {"last_message_id": 1001, "pending_delete_id": 50}},
    )


def test_database_write_with_lost_acknowledgement_cannot_delete_the_only_remaining_copy(
    import_app, monkeypatch
):
    module = import_app("app.cogs.sticky")

    async def check():
        env = environment(module, monkeypatch)
        save = module.repository.update_sticky_message_id.side_effect

        async def uncertain_save(*args, **kwargs):
            await save(*args, **kwargs)
            raise AutoReconnect("write committed, but acknowledgement was lost")

        module.repository.update_sticky_message_id.side_effect = uncertain_save
        await env.cog.on_message(env.message(60, author=SimpleNamespace(id=8, bot=False)))
        assert 50 not in env.state["messages"] and 1001 in env.state["messages"]
        assert env.state["row"]["last_message_id"] == 1001
        module.repository.update_sticky_message_id.side_effect = save
        env.state["send_error"] = discord.Forbidden(
            SimpleNamespace(status=403, reason="Forbidden"), "Cannot send"
        )
        await env.cog.reconcile_stickies()
        assert 1001 in env.state["messages"]
        env.state["send_error"] = None
        await env.cog.reconcile_stickies()
        assert 50 not in env.state["messages"]
        sticky_id = env.state["row"]["last_message_id"]
        assert sticky_id in env.state["messages"] and not env.state["row"].get("pending_delete_id")
        env.channel.send.assert_awaited_once()

    asyncio.run(check())


def test_first_add_creates_the_sticky_without_waiting_for_a_channel_message(
    import_app, monkeypatch
):
    module = import_app("app.cogs.sticky")

    async def check():
        env = environment(module, monkeypatch)
        env.state["row"] = None
        env.state["messages"].clear()
        await env.cog.add.callback(env.cog, interaction(env), env.channel, "First sticky")
        assert env.state["row"]["last_message_id"] == 1001
        assert env.state["row"]["ignore_bots"] is True
        await env.cog.reconcile_stickies()
        assert env.channel.send.await_count == 1

    asyncio.run(check())
