from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import discord
import pytest

from .helpers import flags, prepare


def test_pause_handles_allows_and_preserves_every_other_permission(import_app, monkeypatch):
    module = import_app("app.services.ghosty_pause_service")

    async def check():
        env = prepare(module, monkeypatch)
        original = flags(env.fresh())
        record = await env.service.reserve(env.fresh(), env.members[100], "5m", panel_id=123)
        channel = env.fresh()
        assert (record["expires_at"] - record["started_at"]).total_seconds() == 300
        assert env.state["pause"]["state"] == "active"
        for member_id in (100, 102, 900, module.GHOSTY_BOT_ID):
            assert channel.permissions_for(env.members[member_id]).send_messages
        for member_id in (101, 103):
            assert not channel.permissions_for(env.members[member_id]).send_messages
            assert channel.permissions_for(env.members[member_id]).view_channel
        assert channel.overwrites_for(env.members[100]).use_application_commands is False
        send_mask = discord.Permissions(send_messages=True).value
        for key, (allow, deny) in original.items():
            current_allow, current_deny = flags(channel)[key]
            assert (allow.value & ~send_mask, deny.value & ~send_mask) == (
                current_allow.value & ~send_mask,
                current_deny.value & ~send_mask,
            )
        await env.service.resume(channel, env.members[100], pause_id=record["id"])
        assert env.state["pause"] is None
        assert flags(env.fresh()) == original
        assert env.changed.await_count == 2

    asyncio.run(check())


def test_invites_require_owner_or_mod_and_do_not_take_over_pause(import_app, monkeypatch):
    module = import_app("app.services.ghosty_pause_service")

    async def check():
        env = prepare(module, monkeypatch)
        record = await env.service.reserve(env.fresh(), env.members[100], "12h", panel_id=123)
        with pytest.raises(module.PauseError):
            await env.service.change_member(
                env.fresh(), env.members[101], env.members[103], pause_id=record["id"], add=True
            )
        record = await env.service.change_member(
            env.fresh(), env.members[100], env.members[103], pause_id=record["id"], add=True
        )
        assert env.fresh().permissions_for(env.members[103]).send_messages
        with pytest.raises(module.PauseError):
            await env.service.resume(env.fresh(), env.members[103])
        with pytest.raises(module.PauseError):
            await env.service.reserve(env.fresh(), env.members[100], "5m", panel_id=124)
        record = await env.service.change_member(
            env.fresh(), env.members[102], env.members[103], pause_id=record["id"], add=False
        )
        assert not env.fresh().permissions_for(env.members[103]).send_messages
        with pytest.raises(module.PauseError):
            await env.service.resume(env.fresh(), env.members[100], pause_id="older-pause")
        await env.service.resume(env.fresh(), env.members[102])
        assert env.state["pause"] is None

    asyncio.run(check())


@pytest.mark.parametrize(
    "case",
    ["reset", "other_user", "excluded", "no_dashboard", "invalid_duration", "hit_before_selection"],
)
def test_eligibility_rejects_invalid_or_outdated_requests(import_app, monkeypatch, case):
    module = import_app("app.services.ghosty_pause_service")

    async def check():
        env = prepare(module, monkeypatch)
        member, duration = env.members[100], "5m"
        if case == "reset":
            env.state["stats"]["streak"] = 0
        elif case == "other_user":
            member = env.members[101]
        elif case == "excluded":
            env.config["dashboard_excluded_channels"] = [50]
        elif case == "no_dashboard":
            env.config["dashboard_channel_id"] = None
        elif case == "invalid_duration":
            duration = "13h"
        else:
            assert await env.service.eligible_count(env.fresh(), member) == 5
            env.state["stats"]["streak"] = 0
        with pytest.raises(module.PauseError):
            await env.service.reserve(env.fresh(), member, duration, panel_id=123)
        assert not env.state["edits"] and env.state["pause"] is None

    asyncio.run(check())


@pytest.mark.parametrize("when", ["before", "after"])
def test_failed_apply_restores_permissions_even_when_discord_already_applied_them(
    import_app, monkeypatch, when
):
    module = import_app("app.services.ghosty_pause_service")

    async def check():
        env = prepare(module, monkeypatch)
        before = flags(env.fresh())
        env.state[f"fail_{when}"] = True
        with pytest.raises(module.PauseError):
            await env.service.reserve(env.fresh(), env.members[100], "5m", panel_id=123)
        assert flags(env.fresh()) == before
        assert env.state["pause"] is None

    asyncio.run(check())


def test_expiry_and_failed_restore_recover_after_restart(import_app, monkeypatch):
    module = import_app("app.services.ghosty_pause_service")

    async def check():
        env = prepare(module, monkeypatch)
        before = flags(env.fresh())
        await env.service.reserve(env.fresh(), env.members[100], "5m", panel_id=123)
        env.state["pause"]["expires_at"] = datetime.now(timezone.utc) - timedelta(seconds=1)
        restarted = module.GhostyPauseService(env.bot, env.service.tracker, env.changed)
        env.state["fail_before"] = True
        await restarted.expiry_worker()
        assert env.state["pause"]["state"] == "restoring"
        assert not env.fresh().permissions_for(env.members[101]).send_messages
        env.state["fail_before"] = False
        await restarted.expiry_worker()
        assert env.state["pause"] is None and flags(env.fresh()) == before

    asyncio.run(check())


def test_restore_preserves_moderator_edits_and_new_unrelated_flags(import_app, monkeypatch):
    module = import_app("app.services.ghosty_pause_service")

    async def check():
        env = prepare(module, monkeypatch)
        await env.service.reserve(env.fresh(), env.members[100], "5m", panel_id=123)
        env.state["overwrites"][env.members[100]].send_messages = False
        env.state["overwrites"][env.members[101]].embed_links = False
        env.state["overwrites"][env.members[900]].use_application_commands = False
        await env.service.resume(env.fresh(), env.members[100])
        channel = env.fresh()
        assert channel.overwrites_for(env.members[100]).send_messages is False
        assert channel.overwrites_for(env.members[101]).send_messages is True
        assert channel.overwrites_for(env.members[101]).embed_links is False
        assert channel.overwrites_for(env.members[900]).use_application_commands is False

    asyncio.run(check())


def test_pending_apply_is_restored_by_worker_after_interruption(import_app, monkeypatch):
    module = import_app("app.services.ghosty_pause_service")

    async def check():
        env = prepare(module, monkeypatch)
        before = flags(env.fresh())
        await env.service.reserve(env.fresh(), env.members[100], "5m", panel_id=123)
        env.state["pause"].update(
            state="preparing", changed_at=datetime.now(timezone.utc) - timedelta(minutes=2)
        )
        await env.service.expiry_worker()
        assert env.state["pause"] is None and flags(env.fresh()) == before

    asyncio.run(check())


def test_player_attribution_requires_proof_and_rejects_duplicate_names(import_app, monkeypatch):
    module = import_app("app.services.ghosty_pause_service")
    tracker_module = import_app("app.services.ghosty_service")
    env = prepare(module, monkeypatch)
    owner, other = env.members[100], env.members[101]
    result = SimpleNamespace(
        guild=env.guild,
        author=env.members[module.GHOSTY_BOT_ID],
        channel=env.fresh(),
        id=2,
        reference=None,
        content="<@100>, you've successfully sent 100 goos",
    )
    assert tracker_module.result_belongs_to(result, owner)
    assert not tracker_module.result_belongs_to(result, other)
    result.content = "**\u200eplayer100\u200e <:badge:12345>**, you've successfully sent 100 goos"
    assert tracker_module.result_belongs_to(result, owner)
    other.display_name = owner.display_name
    assert not tracker_module.result_belongs_to(result, owner)
    env.guild.chunked = False
    assert not tracker_module.result_belongs_to(result, owner)


def test_pause_controls_are_persistent_and_offer_only_allowed_durations(import_app, monkeypatch):
    module = import_app("app.services.ghosty_pause_service")
    ui = import_app("app.services.ghosty_pause_ui")

    async def check():
        env = prepare(module, monkeypatch)
        cog = SimpleNamespace(bot=env.bot, pause_service=env.service)
        setup = ui.PauseSetupView(cog, env.members[100], env.fresh(), count=5)
        select = next(item for item in setup.walk_children() if isinstance(item, discord.ui.Select))
        assert [option.value for option in select.options] == list(module.PAUSE_DURATIONS)
        assert min(module.PAUSE_DURATIONS.values()) == 300
        assert max(module.PAUSE_DURATIONS.values()) == 43200
        record = await env.service.reserve(env.fresh(), env.members[100], "5m", panel_id=123)
        view = ui.GhostyPauseView(cog, record, guild_id=1, channel_id=50)
        assert view.is_persistent()
        assert any(
            f"<t:{int(record['expires_at'].timestamp())}:R>" in item.content
            for item in view.walk_children()
            if isinstance(item, discord.ui.TextDisplay)
        )
        ended = ui.GhostyPauseView(cog, record, guild_id=1, channel_id=50, closed=True)
        assert all(
            item.disabled
            for item in ended.walk_children()
            if isinstance(item, discord.ui.UserSelect)
            or (isinstance(item, discord.ui.Button) and item.url is None)
        )

    asyncio.run(check())


@pytest.mark.parametrize("add", [False, True])
@pytest.mark.parametrize("when", ["before", "after"])
def test_interrupted_invite_changes_restore_the_original_grants(import_app, monkeypatch, add, when):
    module = import_app("app.services.ghosty_pause_service")

    async def check():
        env = prepare(module, monkeypatch)
        before = flags(env.fresh())
        record = await env.service.reserve(env.fresh(), env.members[100], "5m", panel_id=123)
        if not add:
            record = await env.service.change_member(
                env.fresh(), env.members[100], env.members[103], pause_id=record["id"], add=True
            )
        env.state[f"fail_{when}"] = True
        with pytest.raises(module.PauseError):
            await env.service.change_member(
                env.fresh(), env.members[100], env.members[103], pause_id=record["id"], add=add
            )
        # A failure before the write may also block the immediate restore.
        env.state["fail_before"] = False
        await env.service.expiry_worker()
        assert env.state["pause"] is None
        assert flags(env.fresh()) == before

    asyncio.run(check())


def test_uncached_moderator_member_overwrite_is_resolved_and_restored(import_app, monkeypatch):
    module = import_app("app.services.ghosty_pause_service")

    async def check():
        env = prepare(module, monkeypatch)
        moderator = env.members[102]
        env.state["overwrites"][moderator] = discord.PermissionOverwrite(
            send_messages=False, embed_links=False
        )
        original = flags(env.fresh())
        env.guild.get_member = lambda member_id: (
            env.members.get(member_id) if member_id != 102 else None
        )
        record = await env.service.reserve(env.fresh(), env.members[100], "5m", panel_id=123)
        assert env.fresh().permissions_for(moderator).send_messages
        env.guild.fetch_member.assert_any_await(102)
        await env.service.resume(env.fresh(), moderator, pause_id=record["id"])
        assert flags(env.fresh()) == original

    asyncio.run(check())
