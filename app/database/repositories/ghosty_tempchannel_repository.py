
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

from pymongo import ASCENDING
from pymongo.errors import DuplicateKeyError

from app.database.session import db


# =========================================================
# COLLECTIONS
# =========================================================

temp_channels = db["ghosty_temp_channels"]
temp_configs = db["ghosty_temp_configs"]


# =========================================================
# DEFAULTS
# =========================================================

DEFAULT_SETTINGS: dict[str, Any] = {
    "enabled": True,
    "default_personal_limit": 1,
    "default_total_limit": 10,
    "max_ordinary_members": 20,
    "category_id": 1562820882794287254,
    "duration_presets": [
        3600,       # 1 hour
        7200,       # 2 hours
        21600,      # 6 hours
        43200,      # 12 hours
        86400,      # 1 day
        259200,     # 3 days
        604800,     # 7 days
    ],
    "staff_role_ids": [],
    "role_limits": [],
}


# =========================================================
# HELPERS
# =========================================================

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _guild_filter(guild_id: int) -> dict[str, str]:
    return {"guild_id": str(guild_id)}


def _channel_filter(
    guild_id: int,
    channel_id: int,
) -> dict[str, str]:
    return {
        "guild_id": str(guild_id),
        "channel_id": str(channel_id),
    }


def _find_many(
    collection: Any,
    query: dict[str, Any],
    sort: list[tuple[str, int]] | None = None,
) -> list[dict[str, Any]]:
    cursor = collection.find(query)

    if sort:
        cursor = cursor.sort(sort)

    return list(cursor)


# =========================================================
# INDEXES
# =========================================================

async def initialize() -> None:
    """Create indexes for temporary-channel storage."""

    await asyncio.gather(
        asyncio.to_thread(
            temp_channels.create_index,
            [
                ("guild_id", ASCENDING),
                ("channel_id", ASCENDING),
            ],
            unique=True,
            name="temp_channel_unique",
        ),
        asyncio.to_thread(
            temp_channels.create_index,
            [
                ("guild_id", ASCENDING),
                ("creator_id", ASCENDING),
                ("status", ASCENDING),
            ],
            name="temp_creator_active",
        ),
        asyncio.to_thread(
            temp_channels.create_index,
            [
                ("guild_id", ASCENDING),
                ("quota_role_id", ASCENDING),
                ("status", ASCENDING),
            ],
            name="temp_role_quota",
        ),
        asyncio.to_thread(
            temp_channels.create_index,
            [
                ("status", ASCENDING),
                ("expires_at", ASCENDING),
            ],
            name="temp_expiry",
        ),
        asyncio.to_thread(
            temp_channels.create_index,
            [
                ("status", ASCENDING),
                ("warning_at", ASCENDING),
                ("warning_sent_at", ASCENDING),
            ],
            name="temp_warning",
        ),
        asyncio.to_thread(
            temp_configs.create_index,
            [("guild_id", ASCENDING)],
            unique=True,
            name="temp_config_guild_unique",
        ),
    )


# =========================================================
# SERVER CONFIGURATION
# =========================================================

async def get_settings(guild_id: int) -> dict[str, Any]:
    """Return server settings merged with defaults."""

    document = await asyncio.to_thread(
        temp_configs.find_one,
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
        temp_configs.update_one,
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
        temp_configs.update_one,
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


# =========================================================
# TEMPORARY CHANNEL RECORDS
# =========================================================

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
        "quota_role_id": (
            str(quota_role_id) if quota_role_id is not None else None
        ),
        "quota_role_name": quota_role_name,
        "member_ids": sorted(
            {str(member_id) for member_id in (member_ids or [])}
            - {str(creator_id)}
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
            temp_channels.insert_one,
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
        temp_channels.find_one,
        _channel_filter(guild_id, channel_id),
    )


async def get_active_channel(
    guild_id: int,
    channel_id: int,
) -> dict[str, Any] | None:
    return await asyncio.to_thread(
        temp_channels.find_one,
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
        temp_channels,
        query,
        [("created_at", ASCENDING)],
    )


async def get_all_active_channels(
    guild_id: int,
) -> list[dict[str, Any]]:
    return await asyncio.to_thread(
        _find_many,
        temp_channels,
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
            temp_channels.aggregate(
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
        str(document["_id"])
        if document["_id"] is not None
        else "default": int(document["count"])
        for document in documents
    }


async def get_active_creator_count(
    guild_id: int,
    creator_id: int,
) -> int:
    return await asyncio.to_thread(
        temp_channels.count_documents,
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
        temp_channels.count_documents,
        {
            **_guild_filter(guild_id),
            "quota_role_id": (
                str(quota_role_id) if quota_role_id is not None else None
            ),
            "status": "active",
        },
    )


# =========================================================
# MEMBERSHIP AND VISIBILITY
# =========================================================

async def update_visibility(
    guild_id: int,
    channel_id: int,
    visibility: str,
) -> bool:
    if visibility not in {"public", "private"}:
        raise ValueError("visibility must be 'public' or 'private'")

    result = await asyncio.to_thread(
        temp_channels.update_one,
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
        document = temp_channels.find_one(
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

        result = temp_channels.update_one(
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
        temp_channels.update_one,
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


# =========================================================
# EXPIRY AND NOTIFICATIONS
# =========================================================

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
        temp_channels.update_one,
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
        temp_channels,
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
        temp_channels.update_one,
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
        temp_channels,
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
        temp_channels.update_one,
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
        temp_channels.update_one,
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
        temp_channels,
        {
            "status": "deleted",
            "deletion_notified_at": None,
        },
        [("deleted_at", ASCENDING)],
    )


# =========================================================
# ADMINISTRATIVE LOOKUPS
# =========================================================

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
        temp_channels,
        query,
        [("created_at", ASCENDING)],
    )


async def get_all_configs() -> list[dict[str, Any]]:
    return await asyncio.to_thread(
        _find_many,
        temp_configs,
        {},
        [("guild_id", ASCENDING)],
    )
