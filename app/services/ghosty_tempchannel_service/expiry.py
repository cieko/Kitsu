from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import discord
from discord.ext import tasks

from app.database.repositories import ghosty_tempchannel_repository as repository

from .constants import (
    DURATION_PRESETS,
    EXPIRY_CHECK_SECONDS,
    MAX_LIFETIME_SECONDS,
    WARNING_MINUTES,
    TempChannelError,
    logger,
    utc_now,
)


class TempChannelExpiry:
    async def extend_channel(
        self,
        owner: discord.Member,
        channel: discord.TextChannel,
        duration_key: str,
    ) -> datetime:
        await self._get_owned_channel(owner, channel)

        if duration_key not in DURATION_PRESETS:
            raise TempChannelError("Choose one of: " + ", ".join(DURATION_PRESETS))

        record = await repository.get_active_channel(
            owner.guild.id,
            channel.id,
        )

        if record is None:
            raise TempChannelError("This temporary channel is no longer active.")

        now = utc_now()
        current_expiry = record["expires_at"]

        if current_expiry.tzinfo is None:
            current_expiry = current_expiry.replace(tzinfo=timezone.utc)

        new_expiry = max(current_expiry, now) + timedelta(seconds=DURATION_PRESETS[duration_key])

        # Do not allow the resulting expiry to exceed seven days
        # from the time the extension is requested.
        if new_expiry > now + timedelta(seconds=MAX_LIFETIME_SECONDS):
            raise TempChannelError(
                "That extension would exceed the seven-day maximum. Choose a shorter duration."
            )

        updated = await repository.extend_expiry(
            owner.guild.id,
            channel.id,
            new_expires_at=new_expiry,
            duration_seconds=DURATION_PRESETS[duration_key],
        )

        if not updated:
            raise TempChannelError("Could not update the channel expiry.")

        return new_expiry

    async def delete_owned_channel(
        self,
        owner: discord.Member,
        channel: discord.TextChannel,
    ) -> None:
        await self._get_owned_channel(owner, channel)
        await self.delete_channel(channel.guild, channel.id)

    async def delete_channel(
        self,
        guild: discord.Guild,
        channel_id: int,
    ) -> None:
        record = await repository.get_active_channel(
            guild.id,
            channel_id,
        )

        if record is None:
            return

        channel = guild.get_channel(channel_id)

        if channel is not None:
            try:
                await channel.delete(reason="Ghosty temporary-channel cleanup")
            except discord.NotFound:
                pass
            # Let other Discord errors reach the caller. The owner panel can
            # report the failure; the expiry worker logs it and retries later.

        marked = await repository.mark_channel_deleted(guild.id, channel_id)

        if marked:
            await self._notify_deletion(guild, record)

    async def _notify_deletion(
        self,
        guild: discord.Guild,
        record: dict[str, Any],
    ) -> None:
        """Notify the creator once, without blocking channel cleanup."""
        try:
            creator = guild.get_member(int(record["creator_id"]))

            if creator is None:
                try:
                    creator = await self.bot.fetch_user(int(record["creator_id"]))
                except discord.HTTPException:
                    creator = None

            if creator is not None:
                await creator.send(
                    f"Your temporary channel **#{record['name']}** "
                    f"in **{guild.name}** has been deleted."
                )

        except discord.HTTPException:
            logger.info(
                "Could not DM creator for deleted temporary channel %s",
                record["channel_id"],
            )
        finally:
            # Record the attempt even when DMs are unavailable.
            await repository.mark_deletion_notified(
                guild.id,
                int(record["channel_id"]),
            )

    @tasks.loop(seconds=EXPIRY_CHECK_SECONDS)
    async def expiry_worker(self) -> None:
        await self._process_due_channels()

    @expiry_worker.before_loop
    async def before_expiry_worker(self) -> None:
        await self.bot.wait_until_ready()

    async def _process_due_channels(self) -> None:
        # Recover notifications if the process stopped after marking a
        # channel deleted but before attempting to notify its creator.
        for record in await repository.get_deleted_pending_notifications():
            guild = self.bot.get_guild(int(record["guild_id"]))

            if guild is None:
                continue

            try:
                await self._notify_deletion(guild, record)
            except Exception:
                logger.exception(
                    "Failed to process deletion notification for channel %s",
                    record["channel_id"],
                )

        now = utc_now()

        # Warn creators 15 minutes before expiry.
        for record in await repository.get_due_warnings(now):
            guild = self.bot.get_guild(int(record["guild_id"]))

            if guild is None:
                continue

            try:
                creator = guild.get_member(int(record["creator_id"]))

                if creator is None:
                    creator = await self.bot.fetch_user(int(record["creator_id"]))

                await creator.send(
                    f"Your temporary channel **#{record['name']}** "
                    f"in **{guild.name}** will expire in approximately "
                    f"{WARNING_MINUTES} minutes."
                )
            except discord.HTTPException:
                logger.info(
                    "Could not send expiry warning for channel %s",
                    record["channel_id"],
                )
            finally:
                # Do not repeatedly warn creators whose DMs are unavailable.
                await repository.mark_warning_sent(
                    int(record["guild_id"]),
                    int(record["channel_id"]),
                )

        # Expiry is persisted in MongoDB, so this also works after restarts.
        for record in await repository.get_due_expirations(now):
            guild = self.bot.get_guild(int(record["guild_id"]))

            if guild is None:
                # Keep the record active; retry when the guild is available.
                continue

            try:
                await self.delete_channel(
                    guild,
                    int(record["channel_id"]),
                )
            except Exception:
                logger.exception(
                    "Failed to process expiry for temporary channel %s",
                    record["channel_id"],
                )
