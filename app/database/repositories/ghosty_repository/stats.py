from __future__ import annotations

import asyncio
from typing import Any

from pymongo import ReturnDocument

from . import state
from .state import _channel_filter, _now


async def initialize() -> None:
    """Create indexes required by Ghosty's tracking system."""

    await asyncio.gather(
        asyncio.to_thread(
            state.channel_stats.create_index,
            [("guild_id", 1), ("channel_id", 1)],
            unique=True,
        ),
        asyncio.to_thread(
            state.gamble_history.create_index,
            [("guild_id", 1), ("source_message_id", 1)],
            unique=True,
        ),
        asyncio.to_thread(
            state.gamble_history.create_index,
            [("guild_id", 1), ("result_message_id", 1)],
            unique=True,
        ),
        asyncio.to_thread(
            state.gamble_history.create_index,
            [("guild_id", 1), ("channel_id", 1), ("created_at", 1)],
        ),
        asyncio.to_thread(
            state.gamble_history.create_index,
            [("guild_id", 1), ("created_at", -1), ("result_message_id", -1)],
        ),
        asyncio.to_thread(
            state.config_collection.create_index,
            [("guild_id", 1), ("key", 1)],
            unique=True,
        ),
    )


async def get_channel_stats(
    guild_id: int,
    channel_id: int,
) -> dict[str, Any] | None:
    return await asyncio.to_thread(
        state.channel_stats.find_one,
        _channel_filter(guild_id, channel_id),
    )


async def get_all_channel_stats(
    guild_id: int,
) -> list[dict[str, Any]]:
    """List channels with recorded activity, without creating empty rows."""
    cursor = state.channel_stats.find({"guild_id": str(guild_id), "total_gambles": {"$gt": 0}})
    return await asyncio.to_thread(list, cursor)


async def update_channel_stats(
    guild_id: int,
    channel_id: int,
    *,
    streak_delta: int,
    streak_loss_delta: int,
    net_pl_delta: int,
    hit_delta: int = 0,
    miss_delta: int = 0,
) -> None:
    """Legacy helper. Prefer record_result_atomically for gamble results."""

    identity = _channel_filter(guild_id, channel_id)

    await asyncio.to_thread(
        state.channel_stats.update_one,
        identity,
        {
            "$inc": {
                "streak": streak_delta,
                "streak_loss": streak_loss_delta,
                "net_pl": net_pl_delta,
                "hits": hit_delta,
                "misses": miss_delta,
                "total_gambles": hit_delta + miss_delta,
            },
            "$set": {"updated_at": _now()},
            "$setOnInsert": identity,
        },
        upsert=True,
    )


async def set_tracker_message(guild_id: int, channel_id: int, message_id: int) -> None:
    """Keep just the active tracker ID on the existing channel statistics."""
    result = await asyncio.to_thread(
        state.channel_stats.update_one,
        _channel_filter(guild_id, channel_id),
        {"$set": {"tracker_message_id": str(message_id)}},
    )
    if result.matched_count != 1:
        raise RuntimeError("Cannot save a tracker without recorded channel statistics")


async def claim_streak_forecast(
    guild_id: int,
    channel_id: int,
    forecast: dict[str, Any],
) -> dict[str, Any] | None:
    """Keep the first candidate set for this streak, without creating rows.

    A hit or manual counter reset changes total_gambles - streak. Comparing it
    in the update also prevents a delayed dashboard from overwriting a new run.
    """
    base = forecast["streak_base"]
    document = await asyncio.to_thread(
        state.channel_stats.find_one_and_update,
        {
            **_channel_filter(guild_id, channel_id),
            "$expr": {
                "$eq": [
                    {"$subtract": ["$total_gambles", "$streak"]},
                    base,
                ]
            },
        },
        [
            {
                "$set": {
                    "streak_forecast": {
                        "$cond": [
                            {
                                "$and": [
                                    {"$eq": ["$streak_forecast.streak_base", base]},
                                    {"$eq": ["$streak_forecast.version", 3]},
                                    {
                                        "$gt": [
                                            {
                                                "$size": {
                                                    "$cond": [
                                                        {"$isArray": "$streak_forecast.candidates"},
                                                        "$streak_forecast.candidates",
                                                        [],
                                                    ]
                                                }
                                            },
                                            0,
                                        ]
                                    },
                                    {
                                        "$in": [
                                            {"$type": "$streak_forecast.hit_at"},
                                            ["int", "long"],
                                        ]
                                    },
                                    {"$gt": ["$streak_forecast.hit_at", 0]},
                                ]
                            },
                            "$streak_forecast",
                            {"$literal": forecast},
                        ]
                    }
                }
            }
        ],
        projection={"_id": 0, "streak_forecast": 1},
        return_document=ReturnDocument.AFTER,
    )
    return document.get("streak_forecast") if document else None
