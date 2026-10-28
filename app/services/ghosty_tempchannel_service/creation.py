from __future__ import annotations

from datetime import timedelta
from typing import Any

import discord

from app.database.repositories import ghosty_tempchannel_repository as repository

from .constants import (
    DEFAULT_CATEGORY_ID,
    DURATION_PRESETS,
    TempChannelError,
    logger,
    utc_now,
)


class TempChannelCreation:
    @staticmethod
    def _deny_application_commands(
        guild: discord.Guild,
        overwrites: dict[Any, discord.PermissionOverwrite],
    ) -> dict[Any, discord.PermissionOverwrite]:
        """Deny slash/context-menu commands without changing category settings."""
        result = {
            target: discord.PermissionOverwrite.from_pair(*overwrite.pair())
            for target, overwrite in overwrites.items()
        }
        result.setdefault(guild.default_role, discord.PermissionOverwrite())
        # A role/member allow can override an @everyone deny, so handle all
        # inherited targets as well as the owner and explicitly added members.
        for overwrite in result.values():
            overwrite.use_application_commands = False
        return result

    async def ensure_command_permissions(self, channel: discord.TextChannel) -> None:
        """Apply the default to older channels when their owner manages them."""
        current = channel.overwrites
        if channel.guild.default_role in current and all(
            overwrite.use_application_commands is False for overwrite in current.values()
        ):
            return
        await channel.edit(
            overwrites=self._deny_application_commands(channel.guild, current),
            reason="Use text commands to manage Ghosty temporary channels",
        )

    @staticmethod
    def _require_creation_permissions(
        guild: discord.Guild, category: discord.CategoryChannel
    ) -> None:
        bot_member = guild.me
        if bot_member is None:
            raise TempChannelError("I couldn't resolve my server membership. Please try again.")
        permissions = category.permissions_for(bot_member)
        required = {
            "view_channel": "View Channel",
            "manage_channels": "Manage Channels",
            "manage_roles": "Manage Permissions (Manage Roles)",
        }
        missing = [label for flag, label in required.items() if not getattr(permissions, flag)]
        if missing:
            raise TempChannelError(
                "I need "
                + ", ".join(f"**{label}**" for label in missing)
                + f" in the **{category.name}** category to set up temporary channels. "
                "Check my role and category permission overrides. Administrator is not required."
            )

    async def create_channel(
        self,
        member: discord.Member,
        name: str,
        duration_key: str,
    ) -> discord.TextChannel:
        guild = member.guild

        await self.initialize()

        if duration_key not in DURATION_PRESETS:
            raise TempChannelError("Choose one of: " + ", ".join(DURATION_PRESETS))

        name = name.strip()
        if not name or len(name) > 100:
            raise TempChannelError("Channel name must contain between 1 and 100 characters.")

        settings = await self.get_settings(guild.id)

        if not settings.get("enabled", True):
            raise TempChannelError("Temporary-channel creation is currently disabled.")

        category_id = int(settings.get("category_id", DEFAULT_CATEGORY_ID))
        category = guild.get_channel(category_id)

        if not isinstance(category, discord.CategoryChannel):
            raise TempChannelError("The configured temporary-channel category was not found.")

        self._require_creation_permissions(guild, category)
        lock = self._guild_lock(guild.id)

        async with lock:
            # Refresh settings while inside the creation lock.
            settings = await self.get_settings(guild.id)

            if not settings.get("enabled", True):
                raise TempChannelError("Temporary-channel creation is currently disabled.")

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
            setup_step = "create the channel"

            try:
                # Let Discord inherit the category at creation. Resubmitting
                # every category overwrite here requires guild-level permission
                # bits the bot may only hold in this category (or not need).
                channel = await guild.create_text_channel(
                    name=name,
                    category=category,
                    reason=(f"Ghosty temporary channel created by {member} ({member.id})"),
                )

                setup_step = "configure channel permissions"
                overwrites = self._deny_application_commands(guild, channel.overwrites)
                overwrites.setdefault(member, discord.PermissionOverwrite()).update(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    use_application_commands=False,
                )

                # Staff retain channel-management access, with the same command
                # default. Discord administrators bypass channel overwrites.
                for staff_role_id in settings.get("staff_role_ids", []):
                    staff_role = guild.get_role(int(staff_role_id))

                    if staff_role is not None:
                        overwrites.setdefault(staff_role, discord.PermissionOverwrite()).update(
                            view_channel=True,
                            send_messages=True,
                            read_message_history=True,
                            manage_channels=True,
                            use_application_commands=False,
                        )

                channel = await channel.edit(
                    overwrites=overwrites,
                    reason="Grant temporary-channel access and disable application commands",
                )

                # Save the active channel record.
                saved = await repository.create_channel_record(
                    guild_id=guild.id,
                    channel_id=channel.id,
                    creator_id=member.id,
                    category_id=category.id,
                    name=channel.name,
                    visibility="public",
                    quota_role_id=(int(role_id) if role_id is not None else None),
                    quota_role_name=str(quota["role_name"]),
                    created_at=now,
                    expires_at=expires_at,
                    duration_seconds=duration,
                )

                if not saved:
                    raise TempChannelError("A database record already exists for this channel.")

            except Exception as exc:
                if channel is not None:
                    try:
                        await channel.delete(reason="Temporary-channel setup failed")
                    except discord.HTTPException:
                        logger.exception(
                            "Could not roll back channel %s",
                            channel.id,
                        )
                if isinstance(exc, discord.Forbidden):
                    logger.warning(
                        "Discord denied temporary-channel setup: guild=%s category=%s step=%s",
                        guild.id,
                        category.id,
                        setup_step,
                    )
                    raise TempChannelError(
                        f"Discord wouldn't let me {setup_step}. "
                        "Check my **Manage Channels** and **Manage Permissions (Manage Roles)** "
                        f"permissions in **{category.name}** and the inherited permission overrides. "
                        "Administrator is not required."
                    ) from exc
                raise

            logger.info(
                "Temporary channel created: guild=%s channel=%s creator=%s quota_role=%s expiry=%s",
                guild.id,
                channel.id,
                member.id,
                role_id,
                expires_at.isoformat(),
            )

            return channel
