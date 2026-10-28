from __future__ import annotations

import asyncio

import discord

from app.database.repositories import sticky_repository as repository

from .constants import logger


class StickyOperations:
    def _lock(self, channel_id: int) -> asyncio.Lock:
        return self._channel_locks.setdefault(channel_id, asyncio.Lock())

    def _validate(self, channel: discord.TextChannel, script: str) -> None:
        content, embed = self.sticky_service.render(script, channel.guild)
        if not content and embed is None:
            raise ValueError("The sticky script must contain text or an embed.")
        permissions = channel.permissions_for(channel.guild.me)
        required = {
            "view_channel": "View Channel",
            "send_messages": "Send Messages",
            "read_message_history": "Read Message History",
        }
        if embed is not None:
            required["embed_links"] = "Embed Links"
        missing = [label for name, label in required.items() if not getattr(permissions, name)]
        if missing:
            raise ValueError(
                "I need these permissions in the sticky channel: " + ", ".join(missing) + "."
            )

    async def _delete_message(self, channel: discord.TextChannel, message_id: int) -> None:
        try:
            await channel.get_partial_message(int(message_id)).delete()
        except discord.NotFound:
            pass

    async def _cleanup_pending(self, channel: discord.TextChannel, sticky: dict) -> None:
        pending_id = sticky.get("pending_delete_id")
        if pending_id:
            if pending_id != sticky.get("last_message_id"):
                await self._delete_message(channel, pending_id)
            await repository.clear_sticky_cleanup(channel.guild.id, channel.id, pending_id)
            sticky.pop("pending_delete_id", None)

    async def _find_current(self, channel: discord.TextChannel, sticky: dict):
        """Recover a delivered copy before considering another send."""
        saved_id = sticky.get("last_message_id")
        message_id = self._unsaved_messages.get(channel.id) or saved_id
        current = None
        if message_id:
            try:
                current = await channel.fetch_message(int(message_id))
            except discord.NotFound:
                pass
        # A process can stop after Discord accepts a send but before MongoDB
        # saves its ID. Also check beyond a stale pointer for that newer copy.
        history_options = {"limit": None, "oldest_first": False}
        if current is not None:
            history_options["after"] = discord.Object(id=current.id)
        duplicates = []
        async for candidate in channel.history(**history_options):
            if (
                self.bot.user
                and candidate.author.id == self.bot.user.id
                and self.sticky_service.matches(candidate, sticky["message_script"], channel.guild)
            ):
                if current is None or candidate.id > current.id:
                    if current is not None:
                        duplicates.append(current.id)
                    current = candidate
                else:
                    duplicates.append(candidate.id)
        for duplicate_id in duplicates:
            await self._delete_message(channel, duplicate_id)
        if current is not None and current.id != saved_id:
            self._unsaved_messages[channel.id] = current.id
            if not await repository.update_sticky_message_id(
                channel.guild.id, channel.id, current.id
            ):
                raise RuntimeError(
                    "The sticky configuration was removed before its message could be saved."
                )
            sticky["last_message_id"] = current.id
        if current is not None:
            self._unsaved_messages.pop(channel.id, None)
        return current

    async def _replace_locked(
        self,
        channel: discord.TextChannel,
        sticky: dict,
        *,
        trigger_id: int | None = None,
        force: bool = False,
    ):
        await self._cleanup_pending(channel, sticky)
        self._validate(channel, sticky["message_script"])
        current = await self._find_current(channel, sticky)
        if current is not None:
            if not force and (trigger_id is None or trigger_id <= current.id):
                return current
            # A deletion failure must abort the replacement. Never post while
            # the previous copy still exists in the channel.
            await self._delete_message(channel, current.id)
        self._unsaved_messages.pop(channel.id, None)
        new_message = await self.sticky_service.post(channel, sticky["message_script"])
        self._unsaved_messages[channel.id] = new_message.id
        if not await repository.update_sticky_message_id(
            channel.guild.id, channel.id, new_message.id
        ):
            raise RuntimeError(
                "The sticky configuration was removed before its message could be saved."
            )
        self._unsaved_messages.pop(channel.id, None)
        return new_message

    async def configure_sticky(
        self,
        channel: discord.TextChannel,
        script: str,
        created_by: int,
        *,
        require_existing: bool = False,
    ):
        self._validate(channel, script)
        async with self._lock(channel.id):
            if (
                require_existing
                and await repository.get_sticky(channel.guild.id, channel.id) is None
            ):
                raise ValueError("That sticky no longer exists.")
            if not await repository.set_sticky(
                channel.guild.id, channel.id, script, created_by=created_by
            ):
                raise RuntimeError("The sticky configuration could not be saved.")
            sticky = await repository.get_sticky(channel.guild.id, channel.id)
            if sticky is None:
                raise RuntimeError("The saved sticky configuration could not be read.")
            await self._replace_locked(channel, sticky, force=True)

    async def remove_sticky(
        self, guild: discord.Guild, channel_id: int, channel: discord.TextChannel | None
    ) -> bool:
        async with self._lock(channel_id):
            sticky = await repository.get_sticky(guild.id, channel_id)
            if sticky is None:
                return False
            await repository.set_sticky_active(guild.id, channel_id, False)
            if isinstance(channel, discord.TextChannel):
                current = await self._find_current(channel, sticky)
                for message_id in {
                    sticky.get("last_message_id"),
                    sticky.get("pending_delete_id"),
                    current.id if current else None,
                } - {None}:
                    await self._delete_message(channel, message_id)
            await repository.delete_sticky(guild.id, channel_id)
            self._unsaved_messages.pop(channel_id, None)
            return True

    async def report_error(self, interaction: discord.Interaction, error: Exception):
        logger.error("Sticky operation failed.", exc_info=(type(error), error, error.__traceback__))
        if isinstance(error, ValueError):
            message = str(error)
        elif isinstance(error, discord.Forbidden):
            message = "Discord denied access to the sticky channel. Check my channel permissions and try again."
        else:
            message = "I couldn’t finish updating the sticky. Its saved configuration is retained; please try again."
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
