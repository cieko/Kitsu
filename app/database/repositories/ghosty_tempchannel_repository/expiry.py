from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any

from pymongo import ASCENDING

from . import state
from .state import _channel_filter, _find_many, _now


async def extend_expiry(
    guild_id: int,
    channel_id: int,
    *,
    new_expires_at: datetime,
    duration_seconds: int,
) -> bool:
    """Set expiry, with the service enforcing the seven-day cap."""

    now = _now()

    if new_expires_at > now + timedelta(days=7):
        raise ValueError("Expiry cannot exceed seven days from now")

    result = await asyncio.to_thread(
        state.temp_channels.update_one,
        {
            **_channel_filter(guild_id, channel_id),
            "status": "active",
        },
        {
            "$set": {
                "expires_at": new_expires_at,
                "duration_seconds": duration_seconds,
                "warning_at": new_expires_at - timedelta(minutes=15),
                "warning_sent_at": None,
                "extended_at": now,
                "updated_at": now,
            }
        },
    )

    return result.modified_count > 0


async def get_due_warnings(
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    now = now or _now()

    return await asyncio.to_thread(
        _find_many,
        state.temp_channels,
        {
            "status": "active",
            "warning_at": {"$lte": now},
            "warning_sent_at": None,
            "expires_at": {"$gt": now},
        },
        [("warning_at", ASCENDING)],
    )


async def mark_warning_sent(
    guild_id: int,
    channel_id: int,
) -> bool:
    result = await asyncio.to_thread(
        state.temp_channels.update_one,
        {
            **_channel_filter(guild_id, channel_id),
            "status": "active",
            "warning_sent_at": None,
        },
        {
            "$set": {
                "warning_sent_at": _now(),
                "updated_at": _now(),
            }
        },
    )

    return result.modified_count > 0


async def get_due_expirations(
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    now = now or _now()

    return await asyncio.to_thread(
        _find_many,
        state.temp_channels,
        {
            "status": "active",
            "expires_at": {"$lte": now},
        },
        [("expires_at", ASCENDING)],
    )


async def mark_channel_deleted(
    guild_id: int,
    channel_id: int,
) -> bool:
    now = _now()

    result = await asyncio.to_thread(
        state.temp_channels.update_one,
        {
            **_channel_filter(guild_id, channel_id),
            "status": "active",
        },
        {
            "$set": {
                "status": "deleted",
                "deleted_at": now,
                "updated_at": now,
            }
        },
    )

    return result.modified_count > 0


async def mark_deletion_notified(
    guild_id: int,
    channel_id: int,
) -> bool:
    result = await asyncio.to_thread(
        state.temp_channels.update_one,
        _channel_filter(guild_id, channel_id),
        {
            "$set": {
                "deletion_notified_at": _now(),
                "updated_at": _now(),
            }
        },
    )

    return result.modified_count > 0


async def get_deleted_pending_notifications() -> list[dict[str, Any]]:
    return await asyncio.to_thread(
        _find_many,
        state.temp_channels,
        {
            "status": "deleted",
            "deletion_notified_at": None,
        },
        [("deleted_at", ASCENDING)],
    )
