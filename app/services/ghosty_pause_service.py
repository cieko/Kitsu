"""Temporary Send Messages reservations with durable permission restoration."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import logging
from typing import Any, Awaitable, Callable
from uuid import uuid4

import discord
from discord.ext import tasks
from pymongo.errors import ConnectionFailure

from app.database.repositories import ghosty_pause_repository as repository
from app.database.repositories import ghosty_repository as tracking
from app.services.ghosty_prediction import utc
from app.services.ghosty_service import GHOSTY_BOT_ID, GhostyService, result_belongs_to


logger = logging.getLogger(__name__)
PAUSE_MOD_ROLE_ID = 1553874776219713566
PAUSE_DURATIONS = {"5m": 300, "10m": 600, "15m": 900, "30m": 1800,
                   "1h": 3600, "3h": 10800, "6h": 21600, "12h": 43200}


class PauseError(Exception):
    """Expected eligibility, access, or restoration failure."""


def is_pause_moderator(member: discord.Member) -> bool:
    return (
        member.id == member.guild.owner_id or member.guild_permissions.administrator
        or any(role.id == PAUSE_MOD_ROLE_ID for role in member.roles)
    )


def is_role(target: Any) -> bool:
    return isinstance(target, discord.Role) or (isinstance(target, discord.Object) and target.type is discord.Role)


def _key(target: Any) -> str:
    return f"{0 if is_role(target) else 1}:{target.id}"


def apply_send_permissions(
    channel: discord.TextChannel, record: dict[str, Any], *, bot_id: int,
    members: dict[int, discord.Member] | None = None,
) -> tuple[dict[Any, discord.PermissionOverwrite], dict[str, Any]]:
    """Only change Send Messages, saving original tri-state values once."""
    updated = deepcopy(record)
    overwrites = {target: discord.PermissionOverwrite.from_pair(*value.pair()) for target, value in channel.overwrites.items()}
    existed = {_key(target) for target in overwrites}
    originals = {f"{row['type']}:{row['id']}": row for row in updated["before"]}
    targets = {_key(target): target for target in overwrites}
    members = members or {}
    protected = {int(record["owner_id"]), bot_id, GHOSTY_BOT_ID, *map(int, record["allowed_ids"])}
    moderator = channel.guild.get_role(PAUSE_MOD_ROLE_ID)
    if moderator is None:
        raise PauseError("The configured moderator role is missing from this server.")
    desired: dict[Any, bool] = {channel.guild.default_role: False, moderator: True}
    for target, overwrite in overwrites.items():
        if is_role(target):
            if target.id == PAUSE_MOD_ROLE_ID:
                desired[target] = True
            elif overwrite.send_messages is True:
                desired[target] = False
        else:
            member = members.get(target.id) or channel.guild.get_member(target.id)
            exempt = target.id in protected or (member is not None and is_pause_moderator(member))
            if exempt:
                desired[target] = True
            elif overwrite.send_messages is True:
                desired[target] = False
    for member_id in protected:
        target = targets.get(f"1:{member_id}") or channel.guild.get_member(member_id) or discord.Object(id=member_id, type=discord.Member)
        desired[target] = True
    # A removed invite must stop receiving its temporary member-level grant.
    for row in updated["before"]:
        if row["type"] == 1 and int(row["id"]) not in protected:
            member = members.get(int(row["id"])) or channel.guild.get_member(int(row["id"]))
            if member is None or not is_pause_moderator(member):
                target = targets.get(f"1:{row['id']}") or member or discord.Object(id=int(row["id"]), type=discord.Member)
                desired[target] = False
    for target, value in desired.items():
        overwrite = overwrites.setdefault(target, discord.PermissionOverwrite())
        key = _key(target)
        if overwrite.send_messages == value and key not in originals:
            continue
        if key not in originals:
            row = {"id": str(target.id), "type": 0 if is_role(target) else 1,
                   "existed": key in existed, "send_messages": overwrite.send_messages, "applied": value}
            updated["before"].append(row)
            originals[key] = row
        elif originals[key]["applied"] != value:
            # The durable preparing record precedes Discord's write. Recovery
            # must handle both the old grant and the newly requested value.
            originals[key]["previous_applied"] = originals[key]["applied"]
        originals[key]["applied"] = value
        overwrite.send_messages = value
    return overwrites, updated


def restore_send_permissions(channel: discord.TextChannel, record: dict[str, Any]) -> dict[Any, discord.PermissionOverwrite]:
    overwrites = {target: discord.PermissionOverwrite.from_pair(*value.pair()) for target, value in channel.overwrites.items()}
    targets = {_key(target): target for target in overwrites}
    for row in record["before"]:
        key = f"{row['type']}:{row['id']}"
        target = targets.get(key)
        # A moderator removed or changed this overwrite during the pause.
        expected = {row["applied"]}
        if "previous_applied" in row:
            expected.add(row["previous_applied"])
        if target is None or overwrites[target].send_messages not in expected:
            continue
        overwrites[target].send_messages = row["send_messages"]
        if not row["existed"] and overwrites[target].is_empty():
            del overwrites[target]
    return overwrites


class GhostyPauseService:
    def __init__(
        self, bot: Any, tracker: GhostyService,
        on_change: Callable[[Any, int, dict[str, Any], bool], Awaitable[None]] | None = None,
    ):
        self.bot, self.tracker, self.on_change = bot, tracker, on_change
        self._locks: dict[tuple[int, int], asyncio.Lock] = {}
        self.expiry_worker.add_exception_type(ConnectionFailure)

    def _lock(self, guild_id: int, channel_id: int) -> asyncio.Lock:
        return self._locks.setdefault((guild_id, channel_id), asyncio.Lock())

    async def start(self) -> None:
        await repository.initialize()
        if not self.expiry_worker.is_running():
            self.expiry_worker.start()

    def stop(self) -> None:
        self.expiry_worker.cancel()

    async def _notify(self, guild: Any, channel_id: int, record: dict[str, Any], active: bool) -> None:
        if self.on_change is not None:
            try:
                await self.on_change(guild, channel_id, record, active)
            except Exception:
                logger.exception("Could not refresh Ghosty pause controls for channel %s.", channel_id)

    async def _fresh(self, guild: discord.Guild, channel_id: int) -> discord.TextChannel:
        channel = await guild.fetch_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            raise PauseError("Pause is available only in ordinary text channels.")
        return channel

    async def _members(self, channel: discord.TextChannel) -> dict[int, discord.Member]:
        resolved = {}
        for target in channel.overwrites:
            if not is_role(target) and channel.guild.get_member(target.id) is None:
                try:
                    resolved[target.id] = await channel.guild.fetch_member(target.id)
                except discord.NotFound:
                    pass
        return resolved

    def _permissions(self, channel: discord.TextChannel) -> None:
        permissions = channel.permissions_for(channel.guild.me)
        required = {"view_channel": "View Channel", "read_message_history": "Read Message History",
                    "send_messages": "Send Messages", "manage_channels": "Manage Channels", "manage_roles": "Manage Permissions"}
        missing = [label for flag, label in required.items() if not getattr(permissions, flag)]
        if missing:
            raise PauseError("I need these channel permissions: " + ", ".join(missing) + ".")
        if channel.guild.get_role(PAUSE_MOD_ROLE_ID) is None:
            raise PauseError("The configured moderator role is missing from this server.")

    async def eligible_count(self, channel: discord.TextChannel, member: discord.Member) -> int:
        if member.guild.id != channel.guild.id or member.bot:
            raise PauseError("Use this command as a server member in its original channel.")
        configured = await tracking.get_config(channel.guild.id, "dashboard_channel_id")
        excluded = await tracking.get_config(channel.guild.id, "dashboard_excluded_channels", [])
        stats = await tracking.get_channel_stats(channel.guild.id, channel.id)
        if not configured or str(channel.id) in {str(value) for value in excluded} or not stats or int(stats.get("total_gambles", 0)) <= 0:
            raise PauseError("This channel must be included in the Ghosty dashboard.")
        self._permissions(channel)
        rows = await repository.get_current_streak(
            channel.guild.id, channel.id, streak=max(0, int(stats.get("streak", 0))), now=datetime.now(timezone.utc),
        )
        count, checked_legacy = 0, 0
        for row in rows:
            if row["outcome"] == "hit":
                break
            if str(row.get("user_id")) != str(member.id):
                continue
            if row.get("attribution_verified") is True:
                count += 1
            elif checked_legacy < 10:
                checked_legacy += 1
                try:
                    result = await channel.fetch_message(int(row["result_message_id"]))
                    source = None
                    command_id = row.get("command_message_id")
                    if command_id and getattr(result, "reference", None):
                        source = await channel.fetch_message(int(command_id))
                    if result_belongs_to(result, member, source):
                        count += 1
                except (discord.NotFound, discord.Forbidden):
                    continue
            if count >= 5:
                return count
        return count

    async def _save(self, guild_id: int, channel_id: int, record: dict[str, Any], **changes: Any) -> dict[str, Any]:
        updated = {**record, **changes, "revision": record["revision"] + 1, "changed_at": datetime.now(timezone.utc)}
        if not await repository.save_pause(guild_id, channel_id, updated, revision=record["revision"]):
            raise PauseError("This pause changed while the operation was running. Reopen its controls.")
        return updated

    async def _activate(self, guild_id: int, channel_id: int, record: dict[str, Any]) -> dict[str, Any]:
        before = [{key: value for key, value in row.items() if key != "previous_applied"} for row in record["before"]]
        return await self._save(guild_id, channel_id, record, state="active", before=before)

    async def set_panel(
        self, channel: discord.TextChannel, member: discord.Member, *, pause_id: str, panel_id: int,
    ) -> dict[str, Any]:
        async with self._lock(channel.guild.id, channel.id):
            record = await self._controlled(channel.guild, channel.id, member, pause_id)
            return await self._save(channel.guild.id, channel.id, record, panel_id=str(panel_id))

    async def reserve(
        self, channel: discord.TextChannel, member: discord.Member, duration: str, *, panel_id: int,
    ) -> dict[str, Any]:
        if duration not in PAUSE_DURATIONS:
            raise PauseError("Choose a duration from 5 minutes to 12 hours using the dropdown.")
        guild, channel_id = channel.guild, channel.id
        async with self._lock(guild.id, channel_id), self.tracker._get_channel_lock(guild.id, channel_id):
            if await repository.get_pause(guild.id, channel_id):
                raise PauseError("This channel already has an active pause or pending permission restoration.")
            fresh = await self._fresh(guild, channel_id)
            count = await self.eligible_count(fresh, member)
            if count < 5:
                raise PauseError(f"You need 5 verified gambles in this channel’s current streak; I can verify {count}.")
            now = datetime.now(timezone.utc)
            record = {"id": uuid4().hex, "owner_id": str(member.id), "allowed_ids": [], "duration": duration,
                      "started_at": now, "expires_at": now + timedelta(seconds=PAUSE_DURATIONS[duration]),
                      "state": "preparing", "before": [], "panel_id": str(panel_id), "revision": 0, "changed_at": now}
            overwrites, record = apply_send_permissions(
                fresh, record, bot_id=self.bot.user.id, members=await self._members(fresh),
            )
            if not await repository.create_pause(guild.id, channel_id, record):
                raise PauseError("Another pause started in this channel. Reopen its controls.")
            try:
                await fresh.edit(overwrites=overwrites, reason=f"Ghosty pause requested by {member.id}")
                record = await self._activate(guild.id, channel_id, record)
            except Exception as error:
                logger.exception("Could not apply Ghosty pause in channel %s.", channel_id)
                await self._recover_failed_change(guild, channel_id)
                raise PauseError("I couldn’t apply the pause. Any pending permission restoration will be retried automatically.") from error
        await self._notify(guild, channel_id, record, True)
        return record

    async def _controlled(
        self, guild: discord.Guild, channel_id: int, member: discord.Member, pause_id: str | None,
    ) -> dict[str, Any]:
        record = await repository.get_pause(guild.id, channel_id)
        if record is None or (pause_id is not None and record["id"] != pause_id):
            raise PauseError("This pause has ended. These controls cannot change a newer pause.")
        if member.guild.id != guild.id or (str(member.id) != record["owner_id"] and not is_pause_moderator(member)):
            raise PauseError("Only the pause owner or a moderator can manage this pause.")
        return record

    async def change_member(
        self, channel: discord.TextChannel, member: discord.Member, target: discord.Member,
        *, pause_id: str, add: bool,
    ) -> dict[str, Any]:
        guild, channel_id = channel.guild, channel.id
        async with self._lock(guild.id, channel_id):
            record = await self._controlled(guild, channel_id, member, pause_id)
            if record["state"] != "active" or utc(record["expires_at"]) <= datetime.now(timezone.utc):
                raise PauseError("This pause has expired or is being restored.")
            if target.guild.id != guild.id:
                raise PauseError("Choose a member of this server.")
            if target.id in {int(record["owner_id"]), self.bot.user.id, GHOSTY_BOT_ID} or is_pause_moderator(target):
                raise PauseError("The pause owner, moderators, and tracking bots already retain access.")
            allowed = set(record["allowed_ids"])
            fresh = await self._fresh(guild, channel_id)
            if add:
                if not fresh.permissions_for(target).view_channel:
                    raise PauseError("That member must already be able to view this channel.")
                allowed.add(str(target.id))
            else:
                if str(target.id) not in allowed:
                    raise PauseError("That member has not been added to this pause.")
                allowed.remove(str(target.id))
            candidate = {**record, "allowed_ids": sorted(allowed)}
            overwrites, candidate = apply_send_permissions(
                fresh, candidate, bot_id=self.bot.user.id, members=await self._members(fresh),
            )
            record = await self._save(guild.id, channel_id, record, allowed_ids=candidate["allowed_ids"],
                                      before=candidate["before"], state="preparing")
            try:
                await fresh.edit(overwrites=overwrites, reason=f"Ghosty pause access changed by {member.id}")
                record = await self._activate(guild.id, channel_id, record)
            except Exception as error:
                logger.exception("Could not change Ghosty pause access in channel %s.", channel_id)
                await self._recover_failed_change(guild, channel_id)
                raise PauseError("I couldn’t update access. Permission restoration will be retried automatically if needed.") from error
        await self._notify(guild, channel_id, record, True)
        return record

    async def resume(
        self, channel: discord.TextChannel, member: discord.Member, *, pause_id: str | None = None,
    ) -> None:
        async with self._lock(channel.guild.id, channel.id):
            record = await self._controlled(channel.guild, channel.id, member, pause_id)
            await self._release_locked(channel.guild, channel.id, record)

    async def _release_locked(self, guild: discord.Guild, channel_id: int, record: dict[str, Any]) -> None:
        record = await self._save(guild.id, channel_id, record, state="restoring")
        try:
            fresh = await self._fresh(guild, channel_id)
        except discord.NotFound:
            fresh = None
        if fresh is not None:
            restored = restore_send_permissions(fresh, record)
            if restored != fresh.overwrites:
                await fresh.edit(overwrites=restored, reason="Restore Send Messages after Ghosty pause")
        if not await repository.clear_pause(guild.id, channel_id, record["id"], revision=record["revision"]):
            raise PauseError("Permissions were restored, but clearing the pause record needs another attempt.")
        await self._notify(guild, channel_id, record, False)

    async def _recover_failed_change(self, guild: discord.Guild, channel_id: int) -> None:
        try:
            record = await repository.get_pause(guild.id, channel_id)
            if record is not None:
                await self._release_locked(guild, channel_id, record)
        except Exception:
            logger.exception("Ghosty pause restoration remains pending for channel %s.", channel_id)

    @tasks.loop(seconds=15)
    async def expiry_worker(self) -> None:
        for entry in await repository.get_all_pauses():
            guild_id, channel_id = int(entry["guild_id"]), int(entry["channel_id"])
            guild = self.bot.get_guild(guild_id)
            if guild is None:
                continue
            try:
                async with self._lock(guild_id, channel_id):
                    record = await repository.get_pause(guild_id, channel_id)
                    if record is None:
                        continue
                    now = datetime.now(timezone.utc)
                    stale = record["state"] == "preparing" and (now - utc(record["changed_at"])).total_seconds() >= 60
                    if record["state"] == "restoring" or stale or utc(record["expires_at"]) <= now:
                        await self._release_locked(guild, channel_id, record)
            except Exception:
                logger.exception("Could not restore Ghosty pause in channel %s; will retry.", channel_id)

    @expiry_worker.before_loop
    async def before_expiry(self) -> None:
        await self.bot.wait_until_ready()
