from __future__ import annotations

from typing import Any

import discord

from app.database.repositories import ghosty_tempchannel_repository as repository

from .constants import TempChannelError


class TempChannelMembership:
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
            raise TempChannelError("This is not an active Ghosty temporary channel.")

        if record["creator_id"] != str(member.id):
            raise TempChannelError("Only the channel creator can perform this action.")

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

        record = await self._get_owned_channel(member, channel)
        staff_roles = await self._get_staff_roles(member.guild)

        # Keep unrelated permissions, but remove inherited access grants when
        # making the channel private. Role/member allows override @everyone.
        overwrites = dict(channel.overwrites)
        access = {
            "view_channel": True,
            "send_messages": True,
            "read_message_history": True,
        }
        allowed_member_ids = {member.id} | {
            int(member_id) for member_id in record.get("member_ids", [])
        }
        bot_member = member.guild.me
        if bot_member is not None:
            allowed_member_ids.add(bot_member.id)

        if visibility == "private":
            staff_role_ids = {role.id for role in staff_roles}
            for target, overwrite in overwrites.items():
                is_role = isinstance(target, discord.Role) or (
                    isinstance(target, discord.Object) and target.type is discord.Role
                )
                allowed_ids = staff_role_ids if is_role else allowed_member_ids
                if target.id not in allowed_ids:
                    for permission in access:
                        if getattr(overwrite, permission) is True:
                            setattr(overwrite, permission, None)

        everyone = overwrites.setdefault(member.guild.default_role, discord.PermissionOverwrite())
        everyone.update(**{permission: visibility == "public" for permission in access})

        # The creator always retains access.
        overwrites.setdefault(member, discord.PermissionOverwrite()).update(**access)

        # Preserve access even when an invited member is absent from the cache.
        for member_id in allowed_member_ids:
            target = member.guild.get_member(member_id)
            if target is None:
                target = discord.Object(id=member_id, type=discord.Member)
            overwrites.setdefault(target, discord.PermissionOverwrite()).update(**access)

        # Reassert protected staff access.
        for role in staff_roles:
            overwrites.setdefault(role, discord.PermissionOverwrite()).update(
                **access, manage_channels=True
            )

        await channel.edit(
            overwrites=self._deny_application_commands(member.guild, overwrites),
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

        await self.ensure_command_permissions(channel)
        success, reason = await repository.add_member(
            owner.guild.id,
            channel.id,
            target.id,
            max_members=max_members,
        )

        if not success:
            messages = {
                "channel_not_active": ("This temporary channel is no longer active."),
                "creator_already_has_access": ("The creator already has access."),
                "already_added": "That member already has access.",
                "member_limit_reached": (
                    f"This channel has reached its {max_members}-member limit."
                ),
                "member_limit_reached_or_changed": (
                    "The member limit was reached. Please try again."
                ),
            }
            raise TempChannelError(messages.get(reason, "Could not add member."))

        try:
            await channel.set_permissions(
                target,
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                use_application_commands=False,
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

        await self.ensure_command_permissions(channel)
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
