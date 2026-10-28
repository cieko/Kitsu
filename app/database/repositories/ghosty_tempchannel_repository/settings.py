from __future__ import annotations

import asyncio
from typing import Any

from pymongo import ASCENDING

from . import state
from .state import DEFAULT_SETTINGS, _find_many, _guild_filter, _now


async def initialize() -> None:
    """Create indexes for temporary-channel storage."""

    await asyncio.gather(
        asyncio.to_thread(
            state.temp_channels.create_index,
            [
                ("guild_id", ASCENDING),
                ("channel_id", ASCENDING),
            ],
            unique=True,
            name="temp_channel_unique",
        ),
        asyncio.to_thread(
            state.temp_channels.create_index,
            [
                ("guild_id", ASCENDING),
                ("creator_id", ASCENDING),
                ("status", ASCENDING),
            ],
            name="temp_creator_active",
        ),
        asyncio.to_thread(
            state.temp_channels.create_index,
            [
                ("guild_id", ASCENDING),
                ("quota_role_id", ASCENDING),
                ("status", ASCENDING),
            ],
            name="temp_role_quota",
        ),
        asyncio.to_thread(
            state.temp_channels.create_index,
            [
                ("status", ASCENDING),
                ("expires_at", ASCENDING),
            ],
            name="temp_expiry",
        ),
        asyncio.to_thread(
            state.temp_channels.create_index,
            [
                ("status", ASCENDING),
                ("warning_at", ASCENDING),
                ("warning_sent_at", ASCENDING),
            ],
            name="temp_warning",
        ),
        asyncio.to_thread(
            state.temp_configs.create_index,
            [("guild_id", ASCENDING)],
            unique=True,
            name="temp_config_guild_unique",
        ),
    )


async def get_settings(guild_id: int) -> dict[str, Any]:
    """Return server settings merged with defaults."""

    document = await asyncio.to_thread(
        state.temp_configs.find_one,
        _guild_filter(guild_id),
    )

    settings = dict(DEFAULT_SETTINGS)

    if document:
        settings.update(document.get("settings", {}))

    return settings


async def set_setting(
    guild_id: int,
    key: str,
    value: Any,
) -> None:
    """Update one server setting."""

    await asyncio.to_thread(
        state.temp_configs.update_one,
        _guild_filter(guild_id),
        {
            "$set": {
                f"settings.{key}": value,
                "updated_at": _now(),
            },
            "$setOnInsert": {
                "guild_id": str(guild_id),
                "created_at": _now(),
            },
        },
        upsert=True,
    )


async def replace_settings(
    guild_id: int,
    settings: dict[str, Any],
) -> None:
    """Replace settings while preserving the config document."""

    await asyncio.to_thread(
        state.temp_configs.update_one,
        _guild_filter(guild_id),
        {
            "$set": {
                "settings": settings,
                "updated_at": _now(),
            },
            "$setOnInsert": {
                "guild_id": str(guild_id),
                "created_at": _now(),
            },
        },
        upsert=True,
    )


async def get_all_configs() -> list[dict[str, Any]]:
    return await asyncio.to_thread(
        _find_many,
        state.temp_configs,
        {},
        [("guild_id", ASCENDING)],
    )
