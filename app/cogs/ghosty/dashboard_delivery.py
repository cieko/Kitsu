from __future__ import annotations

import asyncio

import discord
from discord.ext import tasks

from app.database.repositories import ghosty_repository as repository

from .constants import (
    DASHBOARD_HEADING,
    DASHBOARD_MESSAGE_KEYS,
    DASHBOARD_REFRESH_SECONDS,
    logger,
)


class DashboardDelivery:
    def _dashboard_lock(self, guild_id: int) -> asyncio.Lock:
        return self._dashboard_locks.setdefault(guild_id, asyncio.Lock())

    async def _get_dashboard_messages(
        self,
        guild: discord.Guild,
        channel: discord.TextChannel,
    ) -> list[discord.Message | None]:
        messages: list[discord.Message | None] = []
        for key in DASHBOARD_MESSAGE_KEYS:
            message_id = await repository.get_config(guild.id, key)
            message = None
            if message_id:
                try:
                    message = await channel.fetch_message(int(message_id))
                except discord.NotFound:
                    pass
                # Permission and network failures do not prove a message is
                # missing. Let the caller report them without sending copies.
            messages.append(message)

        # Older setups did not save the heading ID. Recover the existing
        # heading immediately above the market instead of reposting the stack.
        if messages[0] is None and messages[1] is not None and self.bot.user is not None:
            async for candidate in channel.history(limit=1, before=messages[1]):
                if (
                    candidate.author.id == self.bot.user.id
                    and candidate.content == DASHBOARD_HEADING
                ):
                    messages[0] = candidate
                    await repository.set_config(
                        guild.id, DASHBOARD_MESSAGE_KEYS[0], str(candidate.id)
                    )
        return messages

    async def _post_dashboard_stack(
        self,
        guild: discord.Guild,
        channel: discord.TextChannel,
        old_messages: list[discord.Message | None],
    ) -> list[discord.Message]:
        """Prepare the views, remove the old stack, then post one new stack."""
        market_view = await self._build_dashboard_view(guild)
        controls_view = self._build_dashboard_controls_view()

        async def remove_old(message: discord.Message) -> None:
            try:
                await message.delete()
            except discord.NotFound:
                pass

        # Wait for every deletion before the first send. A failed deletion
        # aborts the repost; the next refresh recovers any missing pieces.
        remaining = {
            message.id: message
            for message in old_messages + self._dashboard_pending_cleanup.get(guild.id, [])
            if message is not None
        }
        deleted = await asyncio.gather(
            *(remove_old(message) for message in remaining.values()),
            return_exceptions=True,
        )
        for result in deleted:
            if isinstance(result, BaseException):
                raise result
        new_messages: list[discord.Message] = []
        self._dashboard_pending_cleanup[guild.id] = new_messages
        try:
            payloads = [
                {"content": DASHBOARD_HEADING},
                {"view": market_view},
                {"view": controls_view},
            ]
            for key, payload in zip(DASHBOARD_MESSAGE_KEYS, payloads):
                if "content" in payload:
                    message = await channel.send(payload["content"])
                else:
                    message = await channel.send(**payload)
                new_messages.append(message)
                # Save each delivered piece before sending the next one so a
                # restart can recover an interrupted stack.
                await repository.set_config(guild.id, key, str(message.id))
            await repository.set_config(guild.id, "dashboard_channel_id", str(channel.id))
        except Exception:
            # A failed send must not leave a partial duplicate on the next run.
            for message in list(new_messages):
                try:
                    await remove_old(message)
                    new_messages.remove(message)
                except discord.HTTPException:
                    logger.warning(
                        "Could not remove partial Ghosty dashboard message %s.", message.id
                    )
            raise

        self._dashboard_pending_cleanup.pop(guild.id, None)
        return new_messages

    async def _refresh_dashboard_for_guild(
        self,
        guild: discord.Guild,
        *,
        repost: bool = False,
    ) -> bool:
        """Refresh the live card, or move the stack below pending messages."""
        async with self._dashboard_lock(guild.id):
            channel_id = await repository.get_config(guild.id, "dashboard_channel_id")
            if not channel_id:
                return False
            channel = guild.get_channel(int(channel_id))
            if not isinstance(channel, discord.TextChannel):
                return False

            try:
                messages = await self._get_dashboard_messages(guild, channel)
                trigger_id = self._dashboard_repost_after.get(guild.id)
                move_to_bottom = (
                    trigger_id is not None
                    and messages[0] is not None
                    and trigger_id > messages[0].id
                )
                if repost or move_to_bottom or any(message is None for message in messages):
                    messages = await self._post_dashboard_stack(guild, channel, messages)
                else:
                    market_view = await self._build_dashboard_view(guild)
                    await messages[1].edit(
                        view=market_view,
                        content=None,
                        embeds=[],
                        attachments=[],
                    )
                pending_id = self._dashboard_repost_after.get(guild.id)
                # A message sent after the new heading may split the stack.
                # Leave its request pending so the queued job moves it again.
                if pending_id is not None and pending_id < messages[0].id:
                    self._dashboard_repost_after.pop(guild.id, None)
            except discord.HTTPException:
                logger.exception("Failed to update Ghosty dashboard in guild %s.", guild.id)
                return False
            return True

    @tasks.loop(seconds=DASHBOARD_REFRESH_SECONDS)
    async def refresh_dashboard(self) -> None:
        for guild in self.bot.guilds:
            try:
                await self._refresh_dashboard_for_guild(guild)
            except Exception:
                logger.exception(
                    "Failed to refresh Ghosty dashboard for guild %s.",
                    guild.id,
                )

    @refresh_dashboard.before_loop
    async def before_dashboard_refresh(self) -> None:
        await self.bot.wait_until_ready()

    def _queue_dashboard_refresh(
        self,
        guild: discord.Guild,
        *,
        after_message_id: int | None = None,
    ) -> None:
        if after_message_id is not None:
            self._dashboard_repost_after[guild.id] = max(
                after_message_id,
                self._dashboard_repost_after.get(guild.id, 0),
            )
        self._dashboard_refresh_versions[guild.id] = (
            self._dashboard_refresh_versions.get(guild.id, 0) + 1
        )
        existing = self._dashboard_refresh_tasks.get(guild.id)
        if existing is None or existing.done():
            self._dashboard_refresh_tasks[guild.id] = self._background(
                self._refresh_pending_dashboard(guild)
            )

    async def _refresh_pending_dashboard(self, guild: discord.Guild) -> None:
        try:
            while True:
                version = self._dashboard_refresh_versions[guild.id]
                try:
                    await self._refresh_dashboard_for_guild(guild)
                except Exception:
                    logger.exception(
                        "Could not refresh the Ghosty dashboard in guild %s.", guild.id
                    )
                if version == self._dashboard_refresh_versions[guild.id]:
                    return
        finally:
            self._dashboard_refresh_tasks.pop(guild.id, None)
