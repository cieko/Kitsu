import asyncio

from app.database.session import db


DEFAULT_PREFIX = ",k"


prefix_collection = db["prefix_configs"]


async def get_prefix(guild_id: int) -> str:
    config = await asyncio.to_thread(
        prefix_collection.find_one,
        {"guild_id": guild_id}
    )

    if config:
        return config.get("prefix", DEFAULT_PREFIX).strip() or DEFAULT_PREFIX

    return DEFAULT_PREFIX


async def set_prefix(guild_id: int, prefix: str):
    await asyncio.to_thread(
        prefix_collection.update_one,
        {"guild_id": guild_id},
        {
            "$set": {
                "prefix": prefix,
            }
        },
        upsert=True,
    )


async def reset_prefix(guild_id: int):
    await asyncio.to_thread(
        prefix_collection.delete_one,
        {"guild_id": guild_id}
    )
