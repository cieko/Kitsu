import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiohttp
import discord
import pytest

from .helpers import image_bytes, interaction


def api_error(status, code, *, message="Internal API details should not reach the user"):
    error_type = discord.Forbidden if status == 403 else discord.HTTPException
    return error_type(
        SimpleNamespace(status=status, reason="Request Failed"),
        {"code": code, "message": message},
    )


@pytest.mark.parametrize(
    "kind,status,code,expected",
    [
        (
            "sticker",
            400,
            30039,
            "This server is out of sticker slots. Ask an admin to remove an unused sticker, then try again.",
        ),
        ("emoji", 400, 30008, "out of emoji slots"),
        ("emoji", 400, 30018, "out of animated emoji slots"),
        ("sticker", 403, 50013, "Create Expressions permission"),
        ("sticker", 413, 0, "too large"),
        ("sticker", 400, 50045, "too large"),
        ("sticker", 400, 50035, "couldn't use this sticker file"),
        ("sticker", 400, 50046, "couldn't use this sticker file"),
        ("sticker", 400, 50124, "five seconds or shorter"),
        ("emoji", 429, 0, "wait a little"),
        ("sticker", 503, 0, "Discord is having trouble"),
        ("sticker", 400, 123456, "Ask an admin to check"),
    ],
)
def test_discord_upload_errors_are_readable_without_tracebacks(
    import_app,
    monkeypatch,
    caplog,
    kind,
    status,
    code,
    expected,
):
    module = import_app("app.cogs.steal_emoji")
    monkeypatch.setattr(module.components, "download_file", AsyncMock(return_value=image_bytes()))
    current = interaction()
    target = (
        current.guild.create_sticker if kind == "sticker" else current.guild.create_custom_emoji
    )
    target.side_effect = api_error(
        status,
        code,
        message="Maximum number of stickers reached (5)"
        if code == 30039
        else "Internal API details should not reach the user",
    )
    item = module.StealItem(kind, "wave", 123, "https://example.com/asset", False, "png")
    with caplog.at_level(logging.WARNING):
        asyncio.run(module.StealToServerButton(item).callback(current))
    target.assert_awaited_once()
    current.followup.send.assert_awaited_once()
    sent = current.followup.send.call_args.args[0]
    assert expected in sent
    assert all(
        text not in sent
        for text in (
            "HTTP",
            "error code",
            "Request Failed",
            "Internal API",
            "Maximum number",
            "Successfully",
        )
    )
    assert current.followup.send.call_args.kwargs == {"ephemeral": True}
    assert caplog.records and all(record.exc_info is None for record in caplog.records)
    assert str(code) in caplog.text


@pytest.mark.parametrize(
    "error", [asyncio.TimeoutError(), aiohttp.ClientConnectionError(), discord.RateLimited(2)]
)
def test_temporary_download_failures_are_handled_without_tracebacks(
    import_app, monkeypatch, caplog, error
):
    module = import_app("app.cogs.steal_emoji")
    monkeypatch.setattr(module.components, "download_file", AsyncMock(side_effect=error))
    current = interaction()
    item = module.StealItem("sticker", "wave", 123, "https://example.com/asset", False, "png")
    with caplog.at_level(logging.WARNING):
        asyncio.run(module.StealToServerButton(item).callback(current))
    current.guild.create_sticker.assert_not_awaited()
    current.followup.send.assert_awaited_once()
    assert "try" in current.followup.send.call_args.args[0]
    assert all(record.exc_info is None for record in caplog.records)


def test_lost_upload_response_does_not_suggest_blindly_adding_another_copy(import_app, monkeypatch):
    module = import_app("app.cogs.steal_emoji")
    monkeypatch.setattr(module.components, "download_file", AsyncMock(return_value=image_bytes()))
    current = interaction()
    current.guild.create_sticker.side_effect = aiohttp.ClientConnectionError()
    item = module.StealItem("sticker", "wave", 123, "https://example.com/asset", False, "png")
    asyncio.run(module.StealToServerButton(item).callback(current))
    current.guild.create_sticker.assert_awaited_once()
    assert (
        "Check the server's stickers before trying again" in current.followup.send.call_args.args[0]
    )


@pytest.mark.parametrize("added", [False, True])
def test_reply_delivery_failure_does_not_retry_or_misreport_the_upload(
    import_app, monkeypatch, caplog, added
):
    module = import_app("app.cogs.steal_emoji")
    monkeypatch.setattr(module.components, "download_file", AsyncMock(return_value=image_bytes()))
    current = interaction()
    if not added:
        current.guild.create_sticker.side_effect = api_error(400, 30039)
    current.followup.send.side_effect = api_error(404, 10015)
    item = module.StealItem("sticker", "wave", 123, "https://example.com/asset", False, "png")
    with caplog.at_level(logging.WARNING):
        asyncio.run(module.StealToServerButton(item).callback(current))
    current.guild.create_sticker.assert_awaited_once()
    current.followup.send.assert_awaited_once()
    sent = current.followup.send.call_args.args[0]
    assert ("Successfully added" in sent) is added
    assert all(record.exc_info is None for record in caplog.records)
    assert f"added={added}" in caplog.text


def test_unexpected_bugs_keep_diagnostics_but_not_internal_details_in_the_reply(
    import_app, monkeypatch, caplog
):
    module = import_app("app.cogs.steal_emoji")
    monkeypatch.setattr(
        module.components, "download_file", AsyncMock(side_effect=RuntimeError("private details"))
    )
    current = interaction()
    item = module.StealItem("sticker", "wave", 123, "https://example.com/asset", False, "png")
    with caplog.at_level(logging.ERROR):
        asyncio.run(module.StealToServerButton(item).callback(current))
    assert any(record.exc_info for record in caplog.records)
    assert "private details" not in current.followup.send.call_args.args[0]
    assert "Something went wrong" in current.followup.send.call_args.args[0]
