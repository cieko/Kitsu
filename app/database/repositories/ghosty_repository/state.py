from __future__ import annotations

from datetime import datetime, timezone

from app.database.session import db

channel_stats = db["ghosty_channel_stats"]


gamble_history = db["ghosty_gamble_history"]


config_collection = db["ghosty_configs"]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _channel_filter(guild_id: int, channel_id: int) -> dict[str, str]:
    return {
        "guild_id": str(guild_id),
        "channel_id": str(channel_id),
    }


from app.database.session import client

__all__ = ["client", "channel_stats", "gamble_history", "config_collection"]
