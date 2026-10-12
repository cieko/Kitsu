
import asyncio

from bson import ObjectId

from app.database.session import db


schedule_collection = db["scheduled_pings"]
timezone_collection = db["guild_timezones"]


# --------------------------------------------------
# TIMEZONE
# --------------------------------------------------

async def get_timezone(guild_id: int) -> str:
    config = await asyncio.to_thread(
        timezone_collection.find_one,
        {"guild_id": guild_id},
    )
    return config.get("timezone", "UTC") if config else "UTC"


async def set_timezone(guild_id: int, timezone: str):
    await asyncio.to_thread(
        timezone_collection.update_one,
        {"guild_id": guild_id},
        {"$set": {"timezone": timezone}},
        upsert=True,
    )


# --------------------------------------------------
# ADD SCHEDULE
# --------------------------------------------------

async def add_schedule(
    guild_id: int,
    channel_id: int,
    time: str,
    message: str,
    role_id: int | None = None,
    user_id: int | None = None,
    ping_everyone: bool = False,
    name: str = "Unnamed schedule",
    created_by: int | None = None,
):
    schedule = {
        "guild_id": guild_id,
        "channel_id": channel_id,
        "name": name.strip() or "Unnamed schedule",
        "created_by": created_by,
        "time": time,
        "message": message,
        "role_id": role_id,
        "user_id": user_id,
        "ping_everyone": ping_everyone,
        "active": True,
        "last_sent_date": None,
    }

    result = await asyncio.to_thread(
        schedule_collection.insert_one,
        schedule,
    )
    return str(result.inserted_id)


# --------------------------------------------------
# GET SCHEDULES
# --------------------------------------------------

def _find_schedules(query: dict, sort_by_name: bool = False):
    cursor = schedule_collection.find(query)

    if sort_by_name:
        cursor = cursor.sort([("name", 1), ("time", 1)])

    return list(cursor)


async def get_schedules(guild_id: int):
    return await asyncio.to_thread(
        _find_schedules,
        {"guild_id": guild_id},
        True,
    )


async def get_active_schedules():
    return await asyncio.to_thread(
        _find_schedules,
        {"active": True},
    )


# --------------------------------------------------
# FIND SCHEDULE BY ID OR NAME
# --------------------------------------------------

async def get_schedule(guild_id: int, schedule_id: str):
    query = {"guild_id": guild_id}

    if ObjectId.is_valid(schedule_id):
        query["_id"] = ObjectId(schedule_id)
    else:
        query["name"] = schedule_id

    return await asyncio.to_thread(
        schedule_collection.find_one,
        query,
    )


# --------------------------------------------------
# UPDATE SCHEDULE SCRIPT
# --------------------------------------------------

async def update_schedule_message(
    guild_id: int,
    schedule_id: str,
    message: str,
) -> bool:
    query = {"guild_id": guild_id}

    if ObjectId.is_valid(schedule_id):
        query["_id"] = ObjectId(schedule_id)
    else:
        query["name"] = schedule_id

    result = await asyncio.to_thread(
        schedule_collection.update_one,
        query,
        {"$set": {"message": message}},
    )

    return result.matched_count > 0


# --------------------------------------------------
# RENAME SCHEDULE
# --------------------------------------------------

async def rename_schedule(
    guild_id: int,
    schedule_id: str,
    name: str,
) -> bool:
    name = name.strip()

    if not name:
        return False

    query = {"guild_id": guild_id}

    if ObjectId.is_valid(schedule_id):
        query["_id"] = ObjectId(schedule_id)
    else:
        query["name"] = schedule_id

    result = await asyncio.to_thread(
        schedule_collection.update_one,
        query,
        {"$set": {"name": name}},
    )

    return result.matched_count > 0


# --------------------------------------------------
# DELETE SCHEDULE
# --------------------------------------------------

async def delete_schedule(
    guild_id: int,
    schedule_id: str,
) -> bool:
    query = {"guild_id": guild_id}

    if ObjectId.is_valid(schedule_id):
        query["_id"] = ObjectId(schedule_id)
    else:
        query["name"] = schedule_id

    result = await asyncio.to_thread(
        schedule_collection.delete_one,
        query,
    )

    return result.deleted_count > 0


async def delete_all_schedules(guild_id: int) -> int:
    result = await asyncio.to_thread(
        schedule_collection.delete_many,
        {"guild_id": guild_id},
    )
    return result.deleted_count


# --------------------------------------------------
# ENABLE / PAUSE SCHEDULE
# --------------------------------------------------

async def set_schedule_active(
    guild_id: int,
    schedule_id: str,
    active: bool,
) -> bool:
    query = {"guild_id": guild_id}

    if ObjectId.is_valid(schedule_id):
        query["_id"] = ObjectId(schedule_id)
    else:
        query["name"] = schedule_id

    result = await asyncio.to_thread(
        schedule_collection.update_one,
        query,
        {"$set": {"active": active}},
    )

    return result.matched_count > 0


# --------------------------------------------------
# MARK SCHEDULE AS SENT
# --------------------------------------------------

async def mark_schedule_sent(
    schedule_id,
    local_date: str,
):
    if isinstance(schedule_id, str) and ObjectId.is_valid(schedule_id):
        schedule_id = ObjectId(schedule_id)

    await asyncio.to_thread(
        schedule_collection.update_one,
        {"_id": schedule_id},
        {"$set": {"last_sent_date": local_date}},
    )