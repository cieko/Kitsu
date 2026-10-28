import discord
from discord.ext import commands

from app.database.repositories.prefix_repository import (
    get_prefix,
    set_prefix,
    reset_prefix,
    DEFAULT_PREFIX,
)


class Prefix(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        print("Prefix Cog loaded")

    @commands.command(name="prefix")
    @commands.guild_only()
    async def prefix(self, ctx: commands.Context, action: str = None, new_prefix: str = None):
        guild_id = ctx.guild.id

        # ,k prefix
        if action is None:
            current_prefix = await get_prefix(guild_id)

            await ctx.send(
                f"My current prefix is `{current_prefix}`"
            )
            return

        action = action.casefold()
        if action in {"set", "reset"} and not (
            isinstance(ctx.author, discord.Member)
            and (
                ctx.author.guild_permissions.administrator
                or ctx.author.guild_permissions.manage_guild
            )
        ):
            await ctx.send("You need **Manage Server** permission to change the prefix.")
            return

        # ,k prefix set !
        if action == "set":
            new_prefix = new_prefix.strip() if new_prefix else ""
            if not new_prefix:
                await ctx.send("Please provide a new prefix.")
                return

            if len(new_prefix) > 5:
                await ctx.send("Prefix must be 5 characters or fewer.")
                return

            await set_prefix(guild_id, new_prefix)

            await ctx.send(
                f"Prefix changed to `{new_prefix}`"
            )
            return

        # ,k prefix reset
        if action == "reset":
            await reset_prefix(guild_id)

            await ctx.send(
                f"Prefix reset to `{DEFAULT_PREFIX}`"
            )
            return

        await ctx.send(
            "Usage:\n"
            "`,k prefix`\n"
            "`,k prefix set <prefix>`\n"
            "`,k prefix reset`"
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Prefix(bot))
