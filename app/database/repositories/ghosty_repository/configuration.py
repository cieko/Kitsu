from __future__ import annotations

import asyncio
from typing import Any

from . import state
from .state import _now


async def get_config(
    guild_id: int,
    key: str,
    default: Any = None,
) -> Any:
    document = await asyncio.to_thread(
        state.config_collection.find_one,
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
        state.config_collection.update_one,
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
        state.config_collection.delete_one,
        {"guild_id": str(guild_id), "key": key},
    )
