import asyncio

from app.database.session import db


sticky_collection = db["sticky_messages"]


async def get_sticky(guild_id: int, channel_id: int):
    return await asyncio.to_thread(
        sticky_collection.find_one,
        {
            "guild_id": guild_id,
            "channel_id": channel_id,
        },
    )


async def get_stickies(guild_id: int):
    def _find():
        return list(
            sticky_collection.find(
                {"guild_id": guild_id}
            ).sort("channel_id", 1)
        )

    return await asyncio.to_thread(_find)


async def set_sticky(
    guild_id: int,
    channel_id: int,
    message_script: str,
    created_by: int | None = None,
) -> bool:
    result = await asyncio.to_thread(
        sticky_collection.update_one,
        {
            "guild_id": guild_id,
            "channel_id": channel_id,
        },
        {
            "$set": {
                "message_script": message_script,
                "active": True,
                "created_by": created_by,
            },
            "$setOnInsert": {
                "last_message_id": None,
                "ignore_bots": True,
            },
        },
        upsert=True,
    )

    return result.acknowledged


async def set_sticky_active(
    guild_id: int,
    channel_id: int,
    active: bool,
) -> bool:
    result = await asyncio.to_thread(
        sticky_collection.update_one,
        {
            "guild_id": guild_id,
            "channel_id": channel_id,
        },
        {
            "$set": {"active": active}
        },
    )

    return result.matched_count > 0


async def set_sticky_ignore_bots(
    guild_id: int,
    channel_id: int,
    ignore_bots: bool,
) -> bool:
    result = await asyncio.to_thread(
        sticky_collection.update_one,
        {
            "guild_id": guild_id,
            "channel_id": channel_id,
        },
        {
            "$set": {"ignore_bots": ignore_bots}
        },
    )

    return result.matched_count > 0


async def update_sticky_message_id(
    guild_id: int,
    channel_id: int,
    message_id: int | None,
    *,
    pending_delete_id: int | None = None,
) -> bool:
    result = await asyncio.to_thread(
        sticky_collection.update_one,
        {
            "guild_id": guild_id,
            "channel_id": channel_id,
        },
        {
            "$set": {"last_message_id": message_id, "pending_delete_id": pending_delete_id}
        },
    )
    return result.matched_count > 0


async def clear_sticky_cleanup(guild_id: int, channel_id: int, message_id: int) -> None:
    await asyncio.to_thread(
        sticky_collection.update_one,
        {"guild_id": guild_id, "channel_id": channel_id, "pending_delete_id": message_id},
        {"$unset": {"pending_delete_id": ""}},
    )


async def delete_sticky(
    guild_id: int,
    channel_id: int,
) -> bool:
    result = await asyncio.to_thread(
        sticky_collection.delete_one,
        {
            "guild_id": guild_id,
            "channel_id": channel_id,
        },
    )

    return result.deleted_count > 0
