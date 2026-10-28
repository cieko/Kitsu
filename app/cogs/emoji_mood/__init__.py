from .cog import EmojiMood


async def setup(bot):
    await bot.add_cog(EmojiMood(bot))
