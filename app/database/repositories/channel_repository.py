from app.database.session import db


channel_configs = db["channel_configs"]


async def set_channel_config(
    guild_id: int,
    channel_id: int,
    preset: str,
):
    channel_configs.update_one(
        {
            "guild_id": guild_id,
            "channel_id": channel_id,
        },
        {
            "$set": {
                "guild_id": guild_id,
                "channel_id": channel_id,
                "preset": preset,
            }
        },
        upsert=True,
    )


async def get_channel_config(
    guild_id: int,
    channel_id: int,
):
    return channel_configs.find_one(
        {
            "guild_id": guild_id,
            "channel_id": channel_id,
        }
    )