from app.database.session import db


channel_configs = db["channel_configs"]


async def set_channel_config(
    guild_id: int,
    channel_id: int,
    preset: str,
    rules: dict | None = None,
    gif_domains: list[str] | None = None,
    whitelist_users: list[int] | None = None,
    whitelist_roles: list[int] | None = None,
):
    config = {
        "guild_id": guild_id,
        "channel_id": channel_id,
        "preset": preset,
    }

    if rules is not None:
        config["rules"] = rules

    if gif_domains is not None:
        config["gif_domains"] = gif_domains

    if whitelist_users is not None:
        config["whitelist_users"] = whitelist_users

    if whitelist_roles is not None:
        config["whitelist_roles"] = whitelist_roles

    channel_configs.update_one(
        {
            "guild_id": guild_id,
            "channel_id": channel_id,
        },
        {
            "$set": config,
        },
        upsert=True,
    )

    
async def reset_channel_config(
    guild_id: int,
    channel_id: int,
):
    channel_configs.delete_one(
        {
            "guild_id": guild_id,
            "channel_id": channel_id,
        }
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