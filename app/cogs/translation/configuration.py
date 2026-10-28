from __future__ import annotations

import asyncio
from typing import Any

import discord

from .constants import SERVER_DEFAULT_ID


class TranslationConfiguration:
    async def _get_config(
        self,
        guild_id: int,
        channel_id: int | str,
    ) -> dict[str, Any] | None:
        return await asyncio.to_thread(
            self.configs.find_one,
            {
                "guild_id": str(guild_id),
                "channel_id": str(channel_id),
            },
        )

    async def _save_config(
        self,
        guild_id: int,
        channel_id: int | str,
        target_language: str,
        *,
        scope: str,
    ) -> None:
        await asyncio.to_thread(
            self.configs.update_one,
            {
                "guild_id": str(guild_id),
                "channel_id": str(channel_id),
            },
            {
                "$set": {
                    "guild_id": str(guild_id),
                    "channel_id": str(channel_id),
                    "target_language": target_language,
                    "scope": scope,
                    "enabled": True,
                }
            },
            upsert=True,
        )

    async def _disable_config(
        self,
        guild_id: int,
        channel_id: int | str,
    ) -> None:
        # Keep the document: disabled settings deliberately block inheritance.
        await asyncio.to_thread(
            self.configs.update_one,
            {
                "guild_id": str(guild_id),
                "channel_id": str(channel_id),
            },
            {
                "$set": {
                    "enabled": False,
                }
            },
        )

    async def _reset_config(
        self,
        guild_id: int,
        channel_id: int | str,
    ) -> None:
        # Deleting an override allows the next level to take effect.
        await asyncio.to_thread(
            self.configs.delete_one,
            {
                "guild_id": str(guild_id),
                "channel_id": str(channel_id),
            },
        )

    async def _resolve_config(
        self,
        message: discord.Message,
    ) -> dict[str, Any] | None:
        """Resolve thread > parent channel > server default."""

        guild_id = message.guild.id
        channel = message.channel

        # A thread's own setting takes precedence, including disabled settings.
        if isinstance(channel, discord.Thread):
            config = await self._get_config(guild_id, channel.id)

            if config is not None:
                return config

            # If there is no thread override, inherit from the parent.
            if channel.parent_id is not None:
                config = await self._get_config(
                    guild_id,
                    channel.parent_id,
                )

                if config is not None:
                    return config

        else:
            config = await self._get_config(guild_id, channel.id)

            if config is not None:
                return config

        # Final fallback: server-wide default.
        return await self._get_config(guild_id, SERVER_DEFAULT_ID)
