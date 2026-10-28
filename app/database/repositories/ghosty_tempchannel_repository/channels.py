from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any

from pymongo import ASCENDING
from pymongo.errors import DuplicateKeyError

from . import state
from .state import _channel_filter, _find_many, _guild_filter, _now


async def create_channel_record(
    *,
    guild_id: int,
    channel_id: int,
    creator_id: int,
    category_id: int,
    name: str,
    visibility: str,
    quota_role_id: int | None,
    quota_role_name: str,
    created_at: datetime,
    expires_at: datetime,
    duration_seconds: int,
    member_ids: list[int] | None = None,
) -> bool:
    """
    Insert a channel record.

    quota_role_id=None represents the default member quota.
    Discord channel creation and quota reservation must be coordinated
    by the service layer before this function is called.
    """

    if visibility not in {"public", "private"}:
        raise ValueError("visibility must be 'public' or 'private'")

    document = {
        "guild_id": str(guild_id),
        "channel_id": str(channel_id),
        "creator_id": str(creator_id),
        "category_id": str(category_id),
        "name": name,
        "visibility": visibility,
        "quota_role_id": (str(quota_role_id) if quota_role_id is not None else None),
        "quota_role_name": quota_role_name,
        "member_ids": sorted(
            {str(member_id) for member_id in (member_ids or [])} - {str(creator_id)}
        ),
        "status": "active",
        "created_at": created_at,
        "expires_at": expires_at,
        "duration_seconds": duration_seconds,
        "warning_at": expires_at - timedelta(minutes=15),
        "warning_sent_at": None,
        "deletion_notified_at": None,
        "extended_at": None,
        "deleted_at": None,
        "created_record_at": _now(),
    }

    try:
        await asyncio.to_thread(
            state.temp_channels.insert_one,
            document,
        )
        return True
    except DuplicateKeyError:
        return False


async def get_channel(
    guild_id: int,
    channel_id: int,
) -> dict[str, Any] | None:
    return await asyncio.to_thread(
        state.temp_channels.find_one,
        _channel_filter(guild_id, channel_id),
    )


async def get_active_channel(
    guild_id: int,
    channel_id: int,
) -> dict[str, Any] | None:
    return await asyncio.to_thread(
        state.temp_channels.find_one,
        {
            **_channel_filter(guild_id, channel_id),
            "status": "active",
        },
    )


async def get_creator_channels(
    guild_id: int,
    creator_id: int,
    *,
    active_only: bool = True,
) -> list[dict[str, Any]]:
    query: dict[str, Any] = {
        **_guild_filter(guild_id),
        "creator_id": str(creator_id),
    }

    if active_only:
        query["status"] = "active"

    return await asyncio.to_thread(
        _find_many,
        state.temp_channels,
        query,
        [("created_at", ASCENDING)],
    )


async def get_all_active_channels(
    guild_id: int,
) -> list[dict[str, Any]]:
    return await asyncio.to_thread(
        _find_many,
        state.temp_channels,
        {
            **_guild_filter(guild_id),
            "status": "active",
        },
        [("created_at", ASCENDING)],
    )


async def get_active_counts(
    guild_id: int,
) -> dict[str, int]:
    """
    Return active-channel counts by assigned role quota.

    The key 'default' represents channels assigned to the default quota.
    Other keys are role IDs stored at creation time.
    """

    def _aggregate() -> list[dict[str, Any]]:
        return list(
            state.temp_channels.aggregate(
                [
                    {
                        "$match": {
                            **_guild_filter(guild_id),
                            "status": "active",
                        }
                    },
                    {
                        "$group": {
                            "_id": "$quota_role_id",
                            "count": {"$sum": 1},
                        }
                    },
                ]
            )
        )

    documents = await asyncio.to_thread(_aggregate)

    return {
        str(document["_id"]) if document["_id"] is not None else "default": int(document["count"])
        for document in documents
    }


async def get_active_creator_count(
    guild_id: int,
    creator_id: int,
) -> int:
    return await asyncio.to_thread(
        state.temp_channels.count_documents,
        {
            **_guild_filter(guild_id),
            "creator_id": str(creator_id),
            "status": "active",
        },
    )


async def get_active_role_count(
    guild_id: int,
    quota_role_id: int | None,
) -> int:
    return await asyncio.to_thread(
        state.temp_channels.count_documents,
        {
            **_guild_filter(guild_id),
            "quota_role_id": (str(quota_role_id) if quota_role_id is not None else None),
            "status": "active",
        },
    )


async def list_channels(
    guild_id: int,
    *,
    status: str | None = "active",
) -> list[dict[str, Any]]:
    query: dict[str, Any] = _guild_filter(guild_id)

    if status is not None:
        query["status"] = status

    return await asyncio.to_thread(
        _find_many,
        state.temp_channels,
        query,
        [("created_at", ASCENDING)],
    )
