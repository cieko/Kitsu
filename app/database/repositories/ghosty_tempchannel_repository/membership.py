from __future__ import annotations

import asyncio

from . import state
from .state import _channel_filter, _now


async def update_visibility(
    guild_id: int,
    channel_id: int,
    visibility: str,
) -> bool:
    if visibility not in {"public", "private"}:
        raise ValueError("visibility must be 'public' or 'private'")

    result = await asyncio.to_thread(
        state.temp_channels.update_one,
        {
            **_channel_filter(guild_id, channel_id),
            "status": "active",
        },
        {
            "$set": {
                "visibility": visibility,
                "updated_at": _now(),
            }
        },
    )

    return result.modified_count > 0


async def add_member(
    guild_id: int,
    channel_id: int,
    member_id: int,
    *,
    max_members: int = 20,
) -> tuple[bool, str]:
    """Add one ordinary member, enforcing the channel's member cap."""

    identity = _channel_filter(guild_id, channel_id)
    member = str(member_id)

    def _update() -> tuple[bool, str]:
        document = state.temp_channels.find_one(
            {
                **identity,
                "status": "active",
            }
        )

        if document is None:
            return False, "channel_not_active"

        creator_id = document.get("creator_id")

        if member == creator_id:
            return False, "creator_already_has_access"

        members = document.get("member_ids", [])

        if member in members:
            return False, "already_added"

        if len(members) >= max_members:
            return False, "member_limit_reached"

        result = state.temp_channels.update_one(
            {
                **identity,
                "status": "active",
                "member_ids": {"$ne": member},
                "$expr": {
                    "$lt": [
                        {"$size": {"$ifNull": ["$member_ids", []]}},
                        max_members,
                    ]
                },
            },
            {
                "$addToSet": {"member_ids": member},
                "$set": {"updated_at": _now()},
            },
        )

        if result.modified_count:
            return True, "added"

        return False, "member_limit_reached_or_changed"

    return await asyncio.to_thread(_update)


async def remove_member(
    guild_id: int,
    channel_id: int,
    member_id: int,
) -> bool:
    result = await asyncio.to_thread(
        state.temp_channels.update_one,
        {
            **_channel_filter(guild_id, channel_id),
            "status": "active",
            "member_ids": str(member_id),
        },
        {
            "$pull": {"member_ids": str(member_id)},
            "$set": {"updated_at": _now()},
        },
    )

    return result.modified_count > 0
