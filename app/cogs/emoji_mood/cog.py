from __future__ import annotations

from collections import OrderedDict
import logging
import time

import discord
from discord.ext import commands

from app.services.emoji_mood import MoodClassifier, MoodError

from .assets import message_assets
from .presentation import result_embed


logger = logging.getLogger(__name__)
MAX_ITEMS = 3
MAX_ACTIVE_MESSAGES = 4


class EmojiMood(commands.Cog):
    """Classify emoji/sticker messages; never upload assets or moderate users."""

    def __init__(self, bot, *, classifier=None, enabled=None):
        self.bot = bot
        self.classifier = classifier
        self.enabled = enabled
        self._active = 0
        self._inflight: dict[int, object] = {}
        self._notices: OrderedDict[int, float] = OrderedDict()

    def _available_here(self, message) -> bool:
        if message.guild is None or message.author.bot or message.webhook_id is not None:
            return False
        if message.guild.id != self.bot.main_guild_id:
            return False
        member = getattr(message.guild, "me", None)
        if member is not None and hasattr(message.channel, "permissions_for"):
            permissions = message.channel.permissions_for(member)
            can_send = permissions.send_messages_in_threads if isinstance(message.channel, discord.Thread) else permissions.send_messages
            if not (can_send and permissions.embed_links and permissions.read_message_history):
                return False
        if self.enabled is None:
            from app.config import settings

            self.enabled = settings.emoji_mood_enabled
        return self.enabled

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if self._available_here(message):
            await self._classify_message(message)

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message) -> None:
        if not self._available_here(after):
            return
        previous = {asset.key for asset in message_assets(before)}
        current = {asset.key for asset in message_assets(after)}
        if previous != current:
            self._inflight.pop(after.id, None)
            await self._classify_message(after)

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent) -> None:
        self._inflight.pop(payload.message_id, None)

    async def _reply(self, message, **kwargs):
        if not self.enabled:
            return
        try:
            await message.reply(
                **kwargs, mention_author=False, allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException as error:
            logger.warning("Could not send emoji classification: status=%s code=%s", error.status, error.code)

    async def _notice(self, message, text: str) -> None:
        now = time.monotonic()
        if now - self._notices.get(message.channel.id, -60) < 60:
            return
        self._notices[message.channel.id] = now
        self._notices.move_to_end(message.channel.id)
        while len(self._notices) > 128:
            self._notices.popitem(last=False)
        await self._reply(message, content=text)

    async def _classify_message(self, message) -> None:
        assets = message_assets(message)
        if not assets:
            return
        if self._active >= MAX_ACTIVE_MESSAGES:
            await self._notice(message, "The emoji classifier is busy. Please try again shortly.")
            return
        if self.classifier is None:
            self.classifier = MoodClassifier.from_settings()
        token = object()
        self._inflight[message.id] = token
        self._active += 1
        try:
            embeds = []
            errors = []
            for asset in assets[:MAX_ITEMS]:
                if not self.enabled or self._inflight.get(message.id) is not token:
                    return
                try:
                    result = await self.classifier.classify(asset)
                    embeds.append(result_embed(asset, result))
                except MoodError as error:
                    errors.append(str(error))
            if not self.enabled or self._inflight.get(message.id) is not token:
                return
            if embeds:
                notes = []
                if len(assets) > MAX_ITEMS:
                    notes.append(f"Showing the first {MAX_ITEMS} distinct emojis/stickers.")
                if errors:
                    notes.append("Some emojis/stickers couldn't be classified.")
                await self._reply(message, embeds=embeds, content=" ".join(notes) or None)
            elif errors:
                await self._notice(message, errors[0])
        except Exception:
            logger.exception("Unexpected emoji classification error: message_id=%s", message.id)
            if self._inflight.get(message.id) is token:
                await self._notice(message, "I couldn't classify that emoji or sticker. Please try again later.")
        finally:
            self._active -= 1
            if self._inflight.get(message.id) is token:
                self._inflight.pop(message.id, None)
