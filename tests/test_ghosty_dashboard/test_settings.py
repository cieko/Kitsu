from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from .helpers import prepare_dashboard


@pytest.mark.parametrize("action", ["save", "restore"])
def test_channel_list_changes_repost_stack_then_resume_live_only_refresh(
    import_app,
    monkeypatch,
    action,
):
    module = import_app("app.cogs.ghosty")
    monkeypatch.setattr(
        module.settings_menu,
        "get_application_emoji",
        AsyncMock(
            side_effect=lambda bot, name, fallback: fallback,
        ),
    )
    monkeypatch.setattr(module.asyncio, "sleep", AsyncMock())

    async def check():
        cog, guild, channel, messages, config = prepare_dashboard(module, monkeypatch)
        old_messages = list(messages.values())
        if action == "restore":
            config["dashboard_excluded_channels"] = [5]

        def interaction():
            return SimpleNamespace(
                guild=guild,
                guild_id=guild.id,
                user=SimpleNamespace(guild_permissions=discord.Permissions(manage_guild=True)),
                response=SimpleNamespace(
                    send_message=AsyncMock(),
                    edit_message=AsyncMock(),
                    defer=AsyncMock(),
                ),
                followup=SimpleNamespace(send=AsyncMock()),
                delete_original_response=AsyncMock(),
            )

        controls = cog._build_dashboard_controls_view()
        gear = next(
            item
            for item in controls.walk_children()
            if getattr(item, "custom_id", None) == "ghosty:settings"
        )
        current = interaction()
        await gear.callback(current)
        menu = current.response.send_message.call_args.kwargs["view"]
        current = interaction()
        await menu.children[1].callback(current)
        settings = current.response.send_message.call_args.kwargs["view"]
        channel.send.assert_not_awaited()
        if action == "save":
            toggle = next(
                item
                for item in settings.walk_children()
                if getattr(item, "custom_id", None) == "ghosty:settings:toggle:5"
            )
            current = interaction()
            await toggle.callback(current)
            settings = current.response.edit_message.call_args.kwargs["view"]
            channel.send.assert_not_awaited()

        button = next(
            item
            for item in settings.walk_children()
            if getattr(item, "label", None)
            == ("Save changes" if action == "save" else "Restore all")
        )
        await button.callback(interaction())
        assert config["dashboard_excluded_channels"] == ([5] if action == "save" else [])
        assert channel.send.await_count == 3
        for message in old_messages:
            message.delete.assert_awaited_once()
        stack = [messages[int(config[key])] for key in module.DASHBOARD_MESSAGE_KEYS]
        await cog.refresh_dashboard()
        assert channel.send.await_count == 3
        stack[0].edit.assert_not_awaited()
        stack[1].edit.assert_awaited_once()
        stack[2].edit.assert_not_awaited()

    asyncio.run(check())
