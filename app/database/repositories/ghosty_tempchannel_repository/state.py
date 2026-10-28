from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.database.session import db

temp_channels = db["ghosty_temp_channels"]


temp_configs = db["ghosty_temp_configs"]


DEFAULT_SETTINGS: dict[str, Any] = {
    "enabled": True,
    "default_personal_limit": 1,
    "default_total_limit": 10,
    "max_ordinary_members": 20,
    "category_id": 1562820882794287254,
    "duration_presets": [
        3600,  # 1 hour
        7200,  # 2 hours
        21600,  # 6 hours
        43200,  # 12 hours
        86400,  # 1 day
        259200,  # 3 days
        604800,  # 7 days
    ],
    "staff_role_ids": [],
    "role_limits": [],
}


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
