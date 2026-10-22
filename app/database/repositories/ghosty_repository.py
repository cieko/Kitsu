
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

from pymongo.errors import DuplicateKeyError

from app.database.session import client, db


# ---------------------------------------------------------
# Collections
# ---------------------------------------------------------

channel_stats = db["ghosty_channel_stats"]
user_entries = db["ghosty_user_entries"]
gamble_history = db["ghosty_gamble_history"]
config_collection = db["ghosty_configs"]


# ---------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _channel_filter(guild_id: int, channel_id: int) -> dict[str, str]:
    return {
        "guild_id": str(guild_id),
        "channel_id": str(channel_id),
    }


def _user_filter(
    guild_id: int,
    channel_id: int,
    user_id: int,
) -> dict[str, str]:
    return {
        "guild_id": str(guild_id),
        "channel_id": str(channel_id),
        "user_id": str(user_id),
    }


# ---------------------------------------------------------
# Indexes
# ---------------------------------------------------------

async def initialize() -> None:
    """Create indexes required by Ghosty's tracking system."""

    await asyncio.gather(
        asyncio.to_thread(
            channel_stats.create_index,
            [("guild_id", 1), ("channel_id", 1)],
            unique=True,
        ),
        asyncio.to_thread(
            user_entries.create_index,
            [("guild_id", 1), ("channel_id", 1), ("user_id", 1)],
            unique=True,
        ),
        asyncio.to_thread(
            user_entries.create_index,
            "expires_at",
            expireAfterSeconds=0,
        ),
        asyncio.to_thread(
            gamble_history.create_index,
            [("guild_id", 1), ("source_message_id", 1)],
            unique=True,
        ),
        asyncio.to_thread(
            gamble_history.create_index,
            [("guild_id", 1), ("result_message_id", 1)],
            unique=True,
        ),
        asyncio.to_thread(
            gamble_history.create_index,
            [("guild_id", 1), ("channel_id", 1), ("created_at", 1)],
        ),
        asyncio.to_thread(
            config_collection.create_index,
            [("guild_id", 1), ("key", 1)],
            unique=True,
        ),
    )


# ---------------------------------------------------------
# Channel statistics
# ---------------------------------------------------------

async def get_channel_stats(
    guild_id: int,
    channel_id: int,
) -> dict[str, Any] | None:
    return await asyncio.to_thread(
        channel_stats.find_one,
        _channel_filter(guild_id, channel_id),
    )


async def get_all_channel_stats(
    guild_id: int,
) -> list[dict[str, Any]]:
    cursor = channel_stats.find({"guild_id": str(guild_id)})
    return await asyncio.to_thread(list, cursor)


async def ensure_channel_stats(
    guild_id: int,
    channel_id: int,
) -> dict[str, Any]:
    """Create an empty stats document when none exists."""

    identity = _channel_filter(guild_id, channel_id)

    await asyncio.to_thread(
        channel_stats.update_one,
        identity,
        {
            "$setOnInsert": {
                **identity,
                "streak": 0,
                "streak_loss": 0,
                "net_pl": 0,
                "hits": 0,
                "misses": 0,
                "total_gambles": 0,
                "updated_at": _now(),
            }
        },
        upsert=True,
    )

    result = await get_channel_stats(guild_id, channel_id)

    if result is None:
        raise RuntimeError("Failed to initialize Ghosty channel statistics.")

    return result


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
        channel_stats.update_one,
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


# ---------------------------------------------------------
# Atomic gamble recording
# ---------------------------------------------------------

def _record_result_transaction(
    *,
    guild_id: int,
    channel_id: int,
    user_id: int,
    source_message_id: int,
    result_message_id: int,
    command_message_id: int | None,
    wager: int,
    outcome: str,
    payout: int,
    source: str,
    created_at: datetime | None,
) -> dict[str, Any]:
    """Run history, channel-stat and user-entry changes in one transaction."""

    guild_key = str(guild_id)
    channel_key = str(channel_id)
    user_key = str(user_id)

    history_identity = {
        "guild_id": guild_key,
        "source_message_id": str(source_message_id),
    }
    stats_identity = {
        "guild_id": guild_key,
        "channel_id": channel_key,
    }
    user_identity = {
        "guild_id": guild_key,
        "channel_id": channel_key,
        "user_id": user_key,
    }

    net_pl_delta = payout if outcome == "hit" else -wager
    now = _now()

    history_document = {
        **history_identity,
        **user_identity,
        "result_message_id": str(result_message_id),
        "command_message_id": (
            str(command_message_id) if command_message_id is not None else None
        ),
        "wager": wager,
        "outcome": outcome,
        "payout": payout,
        "net_pl_delta": net_pl_delta,
        "source": source,
        "created_at": created_at or now,
        "recorded_at": now,
    }

    def transaction_body(session) -> dict[str, Any]:
        # Inserting first ensures that a duplicate source message aborts
        # the transaction before any statistics are changed.
        gamble_history.insert_one(
            history_document,
            session=session,
        )

        stats = channel_stats.find_one(
            stats_identity,
            session=session,
        )

        if stats is None:
            stats = {
                **stats_identity,
                "streak": 0,
                "streak_loss": 0,
                "net_pl": 0,
                "hits": 0,
                "misses": 0,
                "total_gambles": 0,
            }

        current_streak = int(stats.get("streak", 0))
        current_loss = int(stats.get("streak_loss", 0))
        streak_net_pl = (
            payout - current_loss
            if outcome == "hit"
            else -wager
        )

        if outcome == "hit":
            new_streak = 0
            new_loss = 0
            hit_delta = 1
            miss_delta = 0
        else:
            new_streak = current_streak + 1
            new_loss = current_loss + wager
            hit_delta = 0
            miss_delta = 1

        channel_stats.update_one(
            stats_identity,
            {
                "$setOnInsert": stats_identity,
                "$set": {
                    "streak": new_streak,
                    "streak_loss": new_loss,
                    "updated_at": now,
                },
                "$inc": {
                    "net_pl": net_pl_delta,
                    "hits": hit_delta,
                    "misses": miss_delta,
                    "total_gambles": 1,
                },
            },
            upsert=True,
            session=session,
        )

        # Update temporary user state within the same transaction.
        user_entry = user_entries.find_one(
            user_identity,
            session=session,
        )

        if outcome == "hit":
            user_update = {
                "$set": {
                    **user_identity,
                    "streak": 0,
                    "streak_loss": 0,
                    "updated_at": now,
                    "expires_at": now + timedelta(minutes=30),
                }
            }
        else:
            user_streak = int(
                user_entry.get("streak", 0) if user_entry else 0
            )
            user_loss = int(
                user_entry.get("streak_loss", 0) if user_entry else 0
            )

            user_update = {
                "$set": {
                    **user_identity,
                    "streak": user_streak + 1,
                    "streak_loss": user_loss + wager,
                    "updated_at": now,
                },
                "$unset": {"expires_at": ""},
            }

        user_entries.update_one(
            user_identity,
            user_update,
            upsert=True,
            session=session,
        )

        updated_stats = channel_stats.find_one(
            stats_identity,
            session=session,
        )

        if updated_stats is None:
            raise RuntimeError("Channel stats disappeared during transaction.")

        last_streak = current_streak

        updated_stats["last_streak"] = last_streak
        updated_stats["streak_net_pl"] = streak_net_pl
        return updated_stats

    with client.start_session() as session:
        return session.with_transaction(transaction_body)


async def record_result_atomically(
    *,
    guild_id: int,
    channel_id: int,
    user_id: int,
    source_message_id: int,
    result_message_id: int,
    command_message_id: int | None = None,
    wager: int,
    outcome: str,
    payout: int,
    source: str = "live",
    created_at: datetime | None = None,
) -> dict[str, Any] | None:
    """Record a result and update all related state atomically.

    Returns updated channel statistics for a new result.
    Returns None if this source message has already been recorded.

    Transaction retries are handled by PyMongo. All database operations
    within the transaction are synchronous and run in the same worker thread.
    """

    if outcome not in {"hit", "miss"}:
        raise ValueError("outcome must be 'hit' or 'miss'")

    if wager <= 0:
        raise ValueError("wager must be greater than zero")

    if payout < 0:
        raise ValueError("payout cannot be negative")

    if outcome == "hit" and payout != wager * 10:
        raise ValueError("A hit payout must equal 10 times the wager.")

    if outcome == "miss" and payout != 0:
        raise ValueError("A miss must have a payout of zero.")

    # A previously recorded response must not be counted again, including
    # legacy records whose source_message_id is the original command ID.
    existing_result = await get_gamble_by_result_message(
        guild_id,
        result_message_id,
    )
    if existing_result is not None:
        return None

    try:
        return await asyncio.to_thread(
            _record_result_transaction,
            guild_id=guild_id,
            channel_id=channel_id,
            user_id=user_id,
            source_message_id=source_message_id,
            result_message_id=result_message_id,
            command_message_id=command_message_id,
            wager=wager,
            outcome=outcome,
            payout=payout,
            source=source,
            created_at=created_at,
        )
    except DuplicateKeyError:
        # Only treat it as an idempotent duplicate if this exact source
        # message already exists. Other unique-index conflicts must surface.
        existing = await get_gamble_by_source_message(
            guild_id,
            source_message_id,
        )
        if existing is not None:
            return None

        existing_result = await get_gamble_by_result_message(
            guild_id,
            result_message_id,
        )
        if existing_result is not None:
            return None

        raise


# ---------------------------------------------------------
# Temporary user entries
# ---------------------------------------------------------

async def get_user_entry(
    guild_id: int,
    channel_id: int,
    user_id: int,
) -> dict[str, Any] | None:
    return await asyncio.to_thread(
        user_entries.find_one,
        _user_filter(guild_id, channel_id, user_id),
    )


async def update_user_entry(
    guild_id: int,
    channel_id: int,
    user_id: int,
    *,
    streak: int,
    streak_loss: int,
    hit: bool,
) -> None:
    """Legacy helper. Atomic recording updates user entries automatically."""

    identity = _user_filter(guild_id, channel_id, user_id)
    now = _now()

    update: dict[str, Any] = {
        "$set": {
            **identity,
            "streak": streak,
            "streak_loss": streak_loss,
            "updated_at": now,
        }
    }

    if hit:
        update["$set"]["expires_at"] = now + timedelta(minutes=30)
    else:
        update["$unset"] = {"expires_at": ""}

    await asyncio.to_thread(
        user_entries.update_one,
        identity,
        update,
        upsert=True,
    )


async def delete_user_entry(
    guild_id: int,
    channel_id: int,
    user_id: int,
) -> None:
    await asyncio.to_thread(
        user_entries.delete_one,
        _user_filter(guild_id, channel_id, user_id),
    )


# ---------------------------------------------------------
# Gamble history
# ---------------------------------------------------------

async def get_gamble_by_source_message(
    guild_id: int,
    source_message_id: int,
) -> dict[str, Any] | None:
    return await asyncio.to_thread(
        gamble_history.find_one,
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
        gamble_history.find_one,
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
        await asyncio.to_thread(gamble_history.insert_one, document)
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
    cursor = (
        gamble_history.find(
            {
                "guild_id": str(guild_id),
                "channel_id": str(channel_id),
            }
        )
        .sort("created_at", 1)
    )

    if limit is not None:
        cursor = cursor.limit(limit)

    return await asyncio.to_thread(list, cursor)


async def has_live_gambles(guild_id: int) -> bool:
    """Return whether this guild already has recorded live results."""
    count = await asyncio.to_thread(
        gamble_history.count_documents,
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
            gamble_history.count_documents,
            {**identity, "outcome": "hit"},
        ),
        asyncio.to_thread(
            gamble_history.count_documents,
            {**identity, "outcome": "miss"},
        ),
        asyncio.to_thread(
            gamble_history.count_documents,
            identity,
        ),
    )

    return {"hits": hits, "misses": misses, "total": total}


# ---------------------------------------------------------
# Configuration
# ---------------------------------------------------------

async def get_config(
    guild_id: int,
    key: str,
    default: Any = None,
) -> Any:
    document = await asyncio.to_thread(
        config_collection.find_one,
        {"guild_id": str(guild_id), "key": key},
    )

    if document is None:
        return default

    return document.get("value", default)


async def set_config(
    guild_id: int,
    key: str,
    value: Any,
) -> None:
    identity = {"guild_id": str(guild_id), "key": key}

    await asyncio.to_thread(
        config_collection.update_one,
        identity,
        {
            "$set": {
                **identity,
                "value": value,
                "updated_at": _now(),
            }
        },
        upsert=True,
    )


async def delete_config(
    guild_id: int,
    key: str,
) -> None:
    await asyncio.to_thread(
        config_collection.delete_one,
        {"guild_id": str(guild_id), "key": key},
    )
