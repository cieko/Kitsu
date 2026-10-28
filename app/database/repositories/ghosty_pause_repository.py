"""A single durable pause record on the existing channel statistics document."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from app.database.repositories import ghosty_repository as tracking


def _scope(guild_id: int, channel_id: int) -> dict[str, str]:
    return {"guild_id": str(guild_id), "channel_id": str(channel_id)}


async def initialize() -> None:
    await asyncio.to_thread(tracking.channel_stats.create_index, "pause.expires_at", sparse=True)


async def get_pause(guild_id: int, channel_id: int) -> dict[str, Any] | None:
    row = await asyncio.to_thread(tracking.channel_stats.find_one, _scope(guild_id, channel_id), {"pause": 1})
    return row.get("pause") if row else None


async def create_pause(guild_id: int, channel_id: int, record: dict[str, Any]) -> bool:
    result = await asyncio.to_thread(
        tracking.channel_stats.update_one,
        {**_scope(guild_id, channel_id), "pause": {"$exists": False}}, {"$set": {"pause": record}},
    )
    return result.modified_count == 1


async def save_pause(guild_id: int, channel_id: int, record: dict[str, Any], *, revision: int) -> bool:
    result = await asyncio.to_thread(
        tracking.channel_stats.update_one,
        {**_scope(guild_id, channel_id), "pause.id": record["id"], "pause.revision": revision},
        {"$set": {"pause": record}},
    )
    return result.matched_count == 1


async def clear_pause(guild_id: int, channel_id: int, pause_id: str, *, revision: int) -> bool:
    result = await asyncio.to_thread(
        tracking.channel_stats.update_one,
        {**_scope(guild_id, channel_id), "pause.id": pause_id, "pause.revision": revision},
        {"$unset": {"pause": ""}},
    )
    return result.matched_count == 1


async def get_all_pauses() -> list[dict[str, Any]]:
    return await asyncio.to_thread(lambda: list(tracking.channel_stats.find(
        {"pause": {"$exists": True}}, {"_id": 0, "guild_id": 1, "channel_id": 1, "pause": 1},
    )))


async def get_current_streak(guild_id: int, channel_id: int, *, streak: int, now: datetime) -> list[dict[str, Any]]:
    if streak <= 0:
        return []
    return await asyncio.to_thread(lambda: list(
        tracking.gamble_history.find(
            {**_scope(guild_id, channel_id), "created_at": {"$lte": now}, "outcome": {"$in": ["hit", "miss"]}},
            {"_id": 0, "user_id": 1, "outcome": 1, "result_message_id": 1,
             "command_message_id": 1, "source_message_id": 1, "attribution_verified": 1},
        ).sort([("created_at", -1), ("result_message_id", -1)]).limit(streak)
    ))
