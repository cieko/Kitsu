import discord
from discord.ext import commands


APPLICATION_EMOJIS = {
    "warning": 1555232371396317224,
    "crosschecked": 1556332674551914569,
    "crossunchecked": 1556332779338334351,
    "tickchecked": 1556331542257598595,
    "tickunchecked": 1556331932927660032,
}


_EMOJI_CACHE: dict[str, str] = {}


async def get_application_emoji(
    bot: commands.Bot,
    emoji_key: str,
    fallback: str,
) -> str:
    """
    Fetch an application emoji once and cache it.

    Subsequent calls for the same emoji key use the cached value
    instead of making another Discord API request.
    """

    if emoji_key in _EMOJI_CACHE:
        return _EMOJI_CACHE[emoji_key]

    emoji_id = APPLICATION_EMOJIS.get(emoji_key)

    if emoji_id is None:
        return fallback

    try:
        app_emoji = await bot.fetch_application_emoji(emoji_id)

        emoji_string = str(app_emoji)

        _EMOJI_CACHE[emoji_key] = emoji_string

        return emoji_string

    except discord.HTTPException:
        return fallback