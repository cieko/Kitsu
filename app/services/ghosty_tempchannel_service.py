
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import discord
from discord.ext import commands, tasks

from app.database.repositories import ghosty_tempchannel_repository as repository

logger = logging.getLogger(__name__)

DEFAULT_CATEGORY_ID = 1562820882794287254
EXPIRY_CHECK_SECONDS = 30
WARNING_MINUTES = 15
MAX_LIFETIME_SECONDS = 7 * 24 * 60 * 60

DURATION_PRESETS: dict[str, int] = {
    "1h": 60 * 60,
    "2h": 2 * 60 * 60,
    "6h": 6 * 60 * 60,
    "12h": 12 * 60 * 60,
    "1d": 24 * 60 * 60,
    "3d": 3 * 24 * 60 * 60,
    "7d": 7 * 24 * 60 * 60,
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class TempChannelError(Exception):
    """Expected temporary-channel operation failure."""


class GhostyTempChannelService:
    """Business logic for Ghosty temporary text channels."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._guild_locks: dict[int, asyncio.Lock] = {}
        self._initialized = False

    def _guild_lock(self, guild_id: int) -> asyncio.Lock:
        return self._guild_locks.setdefault(guild_id, asyncio.Lock())

    async def initialize(self) -> None:
        if self._initialized:
            return

        await repository.initialize()
        self._initialized = True
        logger.info("Ghosty temporary-channel service initialized")

    async def close(self) -> None:
        if self.expiry_worker.is_running():
            self.expiry_worker.cancel()

    async def start(self) -> None:
        await self.initialize()

        if not self.expiry_worker.is_running():
            self.expiry_worker.start()

    # -----------------------------------------------------
    # SETTINGS AND ROLE QUOTAS
    # -----------------------------------------------------

    async def get_settings(self, guild_id: int) -> dict[str, Any]:
        return await repository.get_settings(guild_id)

    async def set_enabled(self, guild_id: int, enabled: bool) -> None:
        await repository.set_setting(guild_id, "enabled", enabled)

    async def set_staff_roles(
        self,
        guild_id: int,
        role_ids: list[int],
    ) -> None:
        await repository.set_setting(
            guild_id,
            "staff_role_ids",
            sorted({str(role_id) for role_id in role_ids}),
        )

    async def set_role_limits(
        self,
        guild_id: int,
        role_id: int,
        personal: int,
        total: int,
        role_name: str,
    ) -> None:
        if personal < 1 or total < 1:
            raise TempChannelError("Limits must both be at least 1.")

        settings = await self.get_settings(guild_id)
        limits = list(settings.get("role_limits", []))

        limits = [
            item
            for item in limits
            if str(item.get("role_id")) != str(role_id)
        ]

        limits.append({
            "role_id": str(role_id),
            "role_name": role_name,
            "personal": personal,
            "total": total,
        })

        await repository.set_setting(guild_id, "role_limits", limits)

    async def remove_role_limits(
        self,
        guild_id: int,
        role_id: int,
    ) -> None:
        settings = await self.get_settings(guild_id)

        limits = [
            item
            for item in settings.get("role_limits", [])
            if str(item.get("role_id")) != str(role_id)
        ]

        await repository.set_setting(guild_id, "role_limits", limits)

    def _applicable_role_limits(
        self,
        member: discord.Member,
        settings: dict[str, Any],
    ) -> dict[str, Any]:
        configured = {
            str(item["role_id"]): item
            for item in settings.get("role_limits", [])
        }

        # Discord role positions determine which configured role is highest.
        for role in sorted(
            member.roles,
            key=lambda item: item.position,
            reverse=True,
        ):
            limit = configured.get(str(role.id))
            if limit:
                return limit

        return {
            "role_id": None,
            "role_name": "Default",
            "personal": int(settings.get("default_personal_limit", 1)),
            "total": int(settings.get("default_total_limit", 10)),
        }

    async def get_member_limits(
        self,
        member: discord.Member,
    ) -> dict[str, Any]:
        settings = await self.get_settings(member.guild.id)
        return self._applicable_role_limits(member, settings)

    # -----------------------------------------------------
    # CHANNEL CREATION
    # -----------------------------------------------------

    async def create_channel(
        self,
        member: discord.Member,
        name: str,
        duration_key: str,
    ) -> discord.TextChannel:
        guild = member.guild

        await self.initialize()

        if duration_key not in DURATION_PRESETS:
            raise TempChannelError(
                "Choose one of: " + ", ".join(DURATION_PRESETS)
            )

        name = name.strip()
        if not name or len(name) > 100:
            raise TempChannelError(
                "Channel name must contain between 1 and 100 characters."
            )

        settings = await self.get_settings(guild.id)

        if not settings.get("enabled", True):
            raise TempChannelError(
                "Temporary-channel creation is currently disabled."
            )

        category_id = int(
            settings.get("category_id", DEFAULT_CATEGORY_ID)
        )
        category = guild.get_channel(category_id)

        if not isinstance(category, discord.CategoryChannel):
            raise TempChannelError(
                "The configured temporary-channel category was not found."
            )

        lock = self._guild_lock(guild.id)

        async with lock:
            # Refresh settings while inside the creation lock.
            settings = await self.get_settings(guild.id)

            if not settings.get("enabled", True):
                raise TempChannelError(
                    "Temporary-channel creation is currently disabled."
                )

            quota = self._applicable_role_limits(member, settings)

            personal_count = await repository.get_active_creator_count(
                guild.id,
                member.id,
            )

            if personal_count >= int(quota["personal"]):
                raise TempChannelError(
                    f"You already have {personal_count} active temporary "
                    f"channel(s). Your limit is {quota['personal']}."
                )

            role_id = quota.get("role_id")
            total_count = await repository.get_active_role_count(
                guild.id,
                int(role_id) if role_id is not None else None,
            )

            if total_count >= int(quota["total"]):
                raise TempChannelError(
                    f"The {quota['role_name']} temporary-channel quota is "
                    f"full ({total_count}/{quota['total']})."
                )

            duration = DURATION_PRESETS[duration_key]
            now = utc_now()
            expires_at = now + timedelta(seconds=duration)

            channel: discord.TextChannel | None = None

            try:
                # Step 1: Create the channel inside the category without
                # supplying custom overwrites.
                channel = await guild.create_text_channel(
                    name=name,
                    category=category,
                    reason=(
                        "Ghosty temporary channel created by "
                        f"{member} ({member.id})"
                    ),
                )

                # Step 2: Explicitly synchronize the new channel with
                # the category before applying individual overrides.
                await channel.edit(
                    sync_permissions=True,
                    reason=(
                        "Synchronize new Ghosty temporary channel "
                        "with its category"
                    ),
                )

                # Step 3: Start with the category's inherited overwrites.
                # Preserve them instead of replacing them with a new dict.
                overwrites = dict(channel.overwrites)

                # Step 4: Give the creator explicit access.
                overwrites[member] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                )

                # Step 5: Ensure configured staff roles retain access
                # and channel-management permissions.
                for staff_role_id in settings.get("staff_role_ids", []):
                    staff_role = guild.get_role(int(staff_role_id))

                    if staff_role is not None:
                        overwrites[staff_role] = (
                            discord.PermissionOverwrite(
                                view_channel=True,
                                send_messages=True,
                                read_message_history=True,
                                manage_channels=True,
                            )
                        )

                # Step 6: Apply the inherited overwrites plus the
                # creator and staff overrides.
                await channel.edit(
                    overwrites=overwrites,
                    reason=(
                        "Grant owner and staff access to Ghosty "
                        "temporary channel"
                    ),
                )

                # Step 7: Save the active channel record.
                saved = await repository.create_channel_record(
                    guild_id=guild.id,
                    channel_id=channel.id,
                    creator_id=member.id,
                    category_id=category.id,
                    name=channel.name,
                    visibility="public",
                    quota_role_id=(
                        int(role_id) if role_id is not None else None
                    ),
                    quota_role_name=str(quota["role_name"]),
                    created_at=now,
                    expires_at=expires_at,
                    duration_seconds=duration,
                )

                if not saved:
                    raise TempChannelError(
                        "A database record already exists for this channel."
                    )

            except Exception:
                if channel is not None:
                    try:
                        await channel.delete(
                            reason="Temporary-channel setup failed"
                        )
                    except discord.HTTPException:
                        logger.exception(
                            "Could not roll back channel %s",
                            channel.id,
                        )
                raise

            logger.info(
                "Temporary channel created: guild=%s channel=%s creator=%s "
                "quota_role=%s expiry=%s",
                guild.id,
                channel.id,
                member.id,
                role_id,
                expires_at.isoformat(),
            )

            return channel

    # -----------------------------------------------------
    # CHANNEL ACCESS AND VISIBILITY
    # -----------------------------------------------------

    async def _get_owned_channel(
        self,
        member: discord.Member,
        channel: discord.abc.GuildChannel,
    ) -> dict[str, Any]:
        if channel.guild.id != member.guild.id:
            raise TempChannelError("That channel belongs to another server.")

        record = await repository.get_active_channel(
            member.guild.id,
            channel.id,
        )

        if record is None:
            raise TempChannelError(
                "This is not an active Ghosty temporary channel."
            )

        if record["creator_id"] != str(member.id):
            raise TempChannelError(
                "Only the channel creator can perform this action."
            )

        return record

    async def _get_staff_roles(
        self,
        guild: discord.Guild,
    ) -> list[discord.Role]:
        settings = await self.get_settings(guild.id)
        return [
            role
            for role_id in settings.get("staff_role_ids", [])
            if (role := guild.get_role(int(role_id))) is not None
        ]

    async def set_visibility(
        self,
        member: discord.Member,
        channel: discord.TextChannel,
        visibility: str,
    ) -> None:
        if visibility not in {"public", "private"}:
            raise TempChannelError("Visibility must be public or private.")

        await self._get_owned_channel(member, channel)
        staff_roles = await self._get_staff_roles(member.guild)

        # Preserve existing category-derived role overwrites.
        overwrites = dict(channel.overwrites)

        if visibility == "public":
            overwrites[member.guild.default_role] = (
                discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                )
            )
        else:
            overwrites[member.guild.default_role] = (
                discord.PermissionOverwrite(
                    view_channel=False,
                    send_messages=False,
                    read_message_history=False,
                )
            )

        # The creator always retains access.
        overwrites[member] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
        )

        # Explicitly added members retain access in private mode.
        record = await repository.get_active_channel(
            member.guild.id,
            channel.id,
        )

        if record is not None:
            for member_id in record.get("member_ids", []):
                target = member.guild.get_member(int(member_id))

                if target is not None:
                    overwrites[target] = discord.PermissionOverwrite(
                        view_channel=True,
                        send_messages=True,
                        read_message_history=True,
                    )

        # Reassert protected staff access.
        for role in staff_roles:
            overwrites[role] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                manage_channels=True,
            )

        await channel.edit(
            overwrites=overwrites,
            reason=f"Temporary-channel visibility changed to {visibility}",
        )

        await repository.update_visibility(
            member.guild.id,
            channel.id,
            visibility,
        )

    async def add_member(
        self,
        owner: discord.Member,
        channel: discord.TextChannel,
        target: discord.Member,
    ) -> None:
        await self._get_owned_channel(owner, channel)

        if target.bot:
            raise TempChannelError("Bots cannot be added as ordinary members.")

        settings = await self.get_settings(owner.guild.id)
        max_members = int(settings.get("max_ordinary_members", 20))

        success, reason = await repository.add_member(
            owner.guild.id,
            channel.id,
            target.id,
            max_members=max_members,
        )

        if not success:
            messages = {
                "channel_not_active": (
                    "This temporary channel is no longer active."
                ),
                "creator_already_has_access": (
                    "The creator already has access."
                ),
                "already_added": "That member already has access.",
                "member_limit_reached": (
                    f"This channel has reached its {max_members}-member limit."
                ),
                "member_limit_reached_or_changed": (
                    "The member limit was reached. Please try again."
                ),
            }
            raise TempChannelError(
                messages.get(reason, "Could not add member.")
            )

        try:
            await channel.set_permissions(
                target,
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                reason="Added by temporary-channel creator",
            )
        except Exception:
            # Avoid leaving the database and Discord permissions inconsistent.
            await repository.remove_member(
                owner.guild.id,
                channel.id,
                target.id,
            )
            raise

    async def remove_member(
        self,
        owner: discord.Member,
        channel: discord.TextChannel,
        target: discord.Member,
    ) -> None:
        await self._get_owned_channel(owner, channel)

        staff_roles = await self._get_staff_roles(owner.guild)

        if any(role in target.roles for role in staff_roles):
            raise TempChannelError("Protected staff cannot be removed.")

        record = await repository.get_active_channel(
            owner.guild.id,
            channel.id,
        )

        if record is None or str(target.id) not in record.get("member_ids", []):
            raise TempChannelError("That member was not explicitly added.")

        await channel.set_permissions(
            target,
            overwrite=None,
            reason="Removed by temporary-channel creator",
        )

        await repository.remove_member(
            owner.guild.id,
            channel.id,
            target.id,
        )

    # -----------------------------------------------------
    # EXPIRY EXTENSIONS
    # -----------------------------------------------------

    async def extend_channel(
        self,
        owner: discord.Member,
        channel: discord.TextChannel,
        duration_key: str,
    ) -> datetime:
        await self._get_owned_channel(owner, channel)

        if duration_key not in DURATION_PRESETS:
            raise TempChannelError(
                "Choose one of: " + ", ".join(DURATION_PRESETS)
            )

        record = await repository.get_active_channel(
            owner.guild.id,
            channel.id,
        )

        if record is None:
            raise TempChannelError(
                "This temporary channel is no longer active."
            )

        now = utc_now()
        current_expiry = record["expires_at"]

        if current_expiry.tzinfo is None:
            current_expiry = current_expiry.replace(tzinfo=timezone.utc)

        new_expiry = max(current_expiry, now) + timedelta(
            seconds=DURATION_PRESETS[duration_key]
        )

        # Do not allow the resulting expiry to exceed seven days
        # from the time the extension is requested.
        if new_expiry > now + timedelta(seconds=MAX_LIFETIME_SECONDS):
            raise TempChannelError(
                "That extension would exceed the seven-day maximum. "
                "Choose a shorter duration."
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

    # -----------------------------------------------------
    # DELETION
    # -----------------------------------------------------

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
                await channel.delete(
                    reason="Ghosty temporary-channel cleanup"
                )
            except discord.NotFound:
                pass
            except discord.HTTPException:
                logger.exception(
                    "Could not delete temporary channel %s",
                    channel_id,
                )
                return

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
                    creator = await self.bot.fetch_user(
                        int(record["creator_id"])
                    )
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

    # -----------------------------------------------------
    # BACKGROUND EXPIRY WORKER
    # -----------------------------------------------------

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
                    creator = await self.bot.fetch_user(
                        int(record["creator_id"])
                    )

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
