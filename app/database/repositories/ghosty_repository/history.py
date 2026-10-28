from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from pymongo.errors import DuplicateKeyError

from . import state
from .state import _channel_filter, _now


async def get_gamble_by_source_message(
    guild_id: int,
    source_message_id: int,
) -> dict[str, Any] | None:
    return await asyncio.to_thread(
        state.gamble_history.find_one,
        {
            "guild_id": str(guild_id),
            "source_message_id": str(source_message_id),
        },
    )


async def get_gamble_by_result_message(
    guild_id: int,
    result_message_id: int,
) -> dict[str, Any] | None:
    """Find a recorded result by Ghosty's response ID.

    Older records use the user's command as source_message_id, so checking
    result_message_id is required for backwards-compatible deduplication.
    """
    return await asyncio.to_thread(
        state.gamble_history.find_one,
        {
            "guild_id": str(guild_id),
            "result_message_id": str(result_message_id),
        },
    )


async def record_gamble(
    *,
    guild_id: int,
    channel_id: int,
    user_id: int,
    source_message_id: int,
    result_message_id: int,
    wager: int,
    outcome: str,
    payout: int,
    source: str = "live",
    created_at: datetime | None = None,
) -> bool:
    """Legacy history-only insert. Prefer record_result_atomically."""

    if outcome not in {"hit", "miss"}:
        raise ValueError("outcome must be 'hit' or 'miss'")

    if wager <= 0 or payout < 0:
        raise ValueError("Invalid wager or payout.")

    document = {
        "guild_id": str(guild_id),
        "channel_id": str(channel_id),
        "user_id": str(user_id),
        "source_message_id": str(source_message_id),
        "result_message_id": str(result_message_id),
        "wager": wager,
        "outcome": outcome,
        "payout": payout,
        "net_pl_delta": payout if outcome == "hit" else -wager,
        "source": source,
        "created_at": created_at or _now(),
        "recorded_at": _now(),
    }

    existing_result = await get_gamble_by_result_message(
        guild_id,
        result_message_id,
    )
    if existing_result is not None:
        return False

    try:
        await asyncio.to_thread(state.gamble_history.insert_one, document)
        return True
    except DuplicateKeyError:
        existing = await get_gamble_by_source_message(
            guild_id,
            source_message_id,
        )
        if existing is not None:
            return False
        existing_result = await get_gamble_by_result_message(
            guild_id,
            result_message_id,
        )
        if existing_result is not None:
            return False
        raise


async def get_gamble_history(
    guild_id: int,
    channel_id: int,
    *,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    cursor = state.gamble_history.find(
        {
            "guild_id": str(guild_id),
            "channel_id": str(channel_id),
        }
    ).sort("created_at", 1)

    if limit is not None:
        cursor = cursor.limit(limit)

    return await asyncio.to_thread(list, cursor)


async def get_prediction_history(guild_id: int) -> list[dict[str, Any]]:
    """Read a bounded, recent training set shared by all channel predictions."""
    cursor = (
        state.gamble_history.find(
            {"guild_id": str(guild_id)},
            {"_id": 0, "channel_id": 1, "outcome": 1, "created_at": 1, "result_message_id": 1},
        )
        .sort([("created_at", -1), ("result_message_id", -1)])
        .limit(20_000)
    )
    return await asyncio.to_thread(list, cursor)


async def get_completed_streak_history(
    guild_id: int,
    channel_id: int,
    *,
    now: datetime,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Read complete runs, including their preceding hit boundary, off-loop."""
    identity = _channel_filter(guild_id, channel_id)
    projection = {
        "_id": 0,
        "created_at": 1,
        "outcome": 1,
        "result_message_id": 1,
        "wager": 1,
        "payout": 1,
    }

    def read() -> list[dict[str, Any]]:
        # Six hit boundaries delimit five complete runs, even if they contain
        # more than 500 results. Do not truncate the losses partway through.
        boundaries = list(
            state.gamble_history.find(
                {**identity, "outcome": "hit", "created_at": {"$lte": now, "$type": "date"}},
                projection,
            )
            .sort([("created_at", -1), ("result_message_id", -1)])
            .limit(max(1, min(int(limit), 5)) + 1)
        )
        if len(boundaries) < 2:
            return []
        newest, oldest = boundaries[0], boundaries[-1]
        rows = list(
            state.gamble_history.find(
                {
                    **identity,
                    "outcome": {"$in": ["hit", "miss"]},
                    "created_at": {"$gte": oldest["created_at"], "$lte": newest["created_at"]},
                },
                projection,
            ).sort([("created_at", 1), ("result_message_id", 1)])
        )

        def key(row: dict[str, Any]) -> tuple[datetime, int]:
            stamp = row["created_at"]
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            return stamp, int(row.get("result_message_id") or 0)

        start, end = key(oldest), key(newest)
        return sorted((row for row in rows if start <= key(row) <= end), key=key)

    return await asyncio.to_thread(read)


async def get_last_gamble_at(
    guild_id: int,
    channel_id: int,
    *,
    before: datetime,
) -> datetime | None:
    """Recover an inactive channel's timing outside the bounded training set."""
    document = await asyncio.to_thread(
        state.gamble_history.find_one,
        {
            **_channel_filter(guild_id, channel_id),
            "outcome": {"$in": ["hit", "miss"]},
            "created_at": {"$lte": before},
        },
        {"_id": 0, "created_at": 1},
        sort=[("created_at", -1), ("result_message_id", -1)],
    )
    return document["created_at"] if document else None


async def has_live_gambles(guild_id: int) -> bool:
    """Return whether this guild already has recorded live results."""
    count = await asyncio.to_thread(
        state.gamble_history.count_documents,
        {
            "guild_id": str(guild_id),
            "source": "live",
        },
        limit=1,
    )
    return count > 0


async def get_gamble_counts(
    guild_id: int,
    channel_id: int,
) -> dict[str, int]:
    identity = {
        "guild_id": str(guild_id),
        "channel_id": str(channel_id),
    }

    hits, misses, total = await asyncio.gather(
        asyncio.to_thread(
            state.gamble_history.count_documents,
            {**identity, "outcome": "hit"},
        ),
        asyncio.to_thread(
            state.gamble_history.count_documents,
            {**identity, "outcome": "miss"},
        ),
        asyncio.to_thread(
            state.gamble_history.count_documents,
            identity,
        ),
    )

    return {"hits": hits, "misses": misses, "total": total}
