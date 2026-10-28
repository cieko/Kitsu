import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from app.cogs.emoji_mood.cog import EmojiMood, MAX_ITEMS
from app.services.emoji_mood import MoodError

from .helpers import classifier, message, result


def test_emoji_message_is_automatically_classified_without_moderation_or_mentions():
    service = classifier()
    current = message()
    cog = EmojiMood(SimpleNamespace(main_guild_id=1), classifier=service, enabled=True)
    asyncio.run(cog.on_message(current))
    service.classify.assert_awaited_once()
    reply = current.reply.call_args.kwargs
    assert "Happy" in reply["embeds"][0].fields[1].value
    assert reply["embeds"][0].fields[2].value == "Unlikely"
    assert reply["embeds"][0].fields[3].name == "Nudity"
    assert reply["embeds"][0].fields[3].value == "Unlikely"
    assert reply["mention_author"] is False and reply["allowed_mentions"].everyone is False
    current.delete.assert_not_awaited()
    assert cog._active == 0 and not cog._inflight


@pytest.mark.parametrize("changes", [
    {"guild": None}, {"guild": SimpleNamespace(id=2)},
    {"author": SimpleNamespace(id=8, bot=True)}, {"webhook_id": 12},
    {"content": "ordinary text"},
])
def test_unrelated_messages_and_bots_do_not_trigger_classification(changes):
    service = classifier()
    current = message(**changes)
    cog = EmojiMood(SimpleNamespace(main_guild_id=1), classifier=service, enabled=True)
    asyncio.run(cog.on_message(current))
    service.classify.assert_not_awaited()
    current.reply.assert_not_awaited()


def test_classifier_can_be_disabled_and_caps_assets_per_message():
    service = classifier()
    current = message("😀😍😢😡😴")
    cog = EmojiMood(SimpleNamespace(main_guild_id=1), classifier=service, enabled=False)
    asyncio.run(cog.on_message(current))
    service.classify.assert_not_awaited()
    cog.enabled = True
    asyncio.run(cog.on_message(current))
    assert service.classify.await_count == MAX_ITEMS
    assert len(current.reply.call_args.kwargs["embeds"]) == MAX_ITEMS
    assert "first 3" in current.reply.call_args.kwargs["content"]


@pytest.mark.parametrize("missing", ["send_messages", "embed_links", "read_message_history"])
def test_no_model_work_is_started_when_the_bot_cannot_reply(missing):
    service = classifier()
    permissions = discord.Permissions(send_messages=True, embed_links=True, read_message_history=True)
    setattr(permissions, missing, False)
    current = message(guild=SimpleNamespace(id=1, me=object()), channel=SimpleNamespace(
        id=3, permissions_for=lambda member: permissions,
    ))
    cog = EmojiMood(SimpleNamespace(main_guild_id=1), classifier=service, enabled=True)
    asyncio.run(cog.on_message(current))
    service.classify.assert_not_awaited()
    current.reply.assert_not_awaited()


def test_edits_only_reclassify_when_assets_change():
    async def check():
        service = classifier()
        cog = EmojiMood(SimpleNamespace(main_guild_id=1), classifier=service, enabled=True)
        before = message("hello 😀")
        after = message("updated words 😀")
        await cog.on_message_edit(before, after)
        service.classify.assert_not_awaited()
        changed = message("😢")
        await cog.on_message_edit(after, changed)
        service.classify.assert_awaited_once()
        changed.reply.assert_awaited_once()

    asyncio.run(check())


@pytest.mark.parametrize("deleted", [False, True])
def test_deleted_or_edited_away_emoji_does_not_publish_a_stale_result(deleted):
    async def check():
        entered, release = asyncio.Event(), asyncio.Event()

        async def classify(asset):
            entered.set()
            await release.wait()
            return result()

        service = SimpleNamespace(classify=AsyncMock(side_effect=classify))
        cog = EmojiMood(SimpleNamespace(main_guild_id=1), classifier=service, enabled=True)
        current = message()
        task = asyncio.create_task(cog.on_message(current))
        await entered.wait()
        if deleted:
            await cog.on_raw_message_delete(SimpleNamespace(message_id=current.id))
        else:
            await cog.on_message_edit(current, message("no emoji anymore"))
        release.set()
        await task
        current.reply.assert_not_awaited()
        assert not cog._inflight and cog._active == 0

    asyncio.run(check())


def test_unavailable_classifier_does_not_spam_the_channel_or_claim_a_mood():
    async def check():
        service = classifier()
        service.classify.side_effect = MoodError("Classifier unavailable.")
        cog = EmojiMood(SimpleNamespace(main_guild_id=1), classifier=service, enabled=True)
        first, second = message(), message(id=100)
        await cog.on_message(first)
        await cog.on_message(second)
        first.reply.assert_awaited_once()
        assert first.reply.call_args.kwargs["content"] == "Classifier unavailable."
        assert "embeds" not in first.reply.call_args.kwargs
        second.reply.assert_not_awaited()

    asyncio.run(check())


def test_reply_permission_failure_is_handled_without_deleting_the_message():
    current = message()
    current.reply.side_effect = discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "Missing Permissions")
    cog = EmojiMood(SimpleNamespace(main_guild_id=1), classifier=classifier(), enabled=True)
    asyncio.run(cog.on_message(current))
    current.delete.assert_not_awaited()
    assert not cog._inflight and cog._active == 0
