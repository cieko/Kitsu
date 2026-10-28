from __future__ import annotations

import asyncio
from typing import Any

import discord
from discord.ext import commands
from pymongo.errors import ConnectionFailure

from app.database.repositories import ghosty_tempchannel_repository as repository

from .constants import TempChannelError, logger
from .creation import TempChannelCreation
from .expiry import TempChannelExpiry
from .membership import TempChannelMembership


class GhostyTempChannelService(TempChannelCreation, TempChannelMembership, TempChannelExpiry):
    """Business logic for Ghosty temporary text channels."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._guild_locks: dict[int, asyncio.Lock] = {}
        self._initialized = False
        self.expiry_worker.add_exception_type(ConnectionFailure)

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

        limits = [item for item in limits if str(item.get("role_id")) != str(role_id)]

        limits.append(
            {
                "role_id": str(role_id),
                "role_name": role_name,
                "personal": personal,
                "total": total,
            }
        )

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
        configured = {str(item["role_id"]): item for item in settings.get("role_limits", [])}

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
