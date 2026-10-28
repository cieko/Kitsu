from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from .constants import logger
from .extraction import get_message_items
from .models import StealItem
from .view import StealEmojiView


class StealEmoji(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def show_results(
        self,
        *,
        items: list[StealItem],
        owner_id: int,
        ctx: commands.Context | None = None,
        interaction: discord.Interaction | None = None,
    ) -> None:
        if not items:
            message = "I couldn't find any custom emojis or stickers in that message."

            if interaction is not None:
                await interaction.response.send_message(
                    message,
                    ephemeral=True,
                )
            elif ctx is not None:
                await ctx.reply(
                    message,
                    mention_author=False,
                )

            return

        # This flag determines whether the second container appears.
        view = StealEmojiView(
            items=items,
            owner_id=owner_id,
            is_slash=interaction is not None,
        )

        if interaction is not None:
            await interaction.response.send_message(
                view=view,
                ephemeral=True,
            )
            view.message = await interaction.original_response()

        elif ctx is not None:
            # Prefix-command previews are public.
            view.message = await ctx.reply(
                view=view,
                mention_author=False,
            )

        view.start_expiry_timer()

    @commands.command(name="steal")
    async def steal_prefix(
        self,
        ctx: commands.Context,
    ) -> None:
        reference = ctx.message.reference

        logger.info(
            "[STEAL] Prefix command | user=%s | message_id=%s",
            ctx.author,
            ctx.message.id,
        )

        if reference is None:
            await ctx.reply(
                "Reply to a message containing emojis or stickers, then run the steal command.",
                mention_author=False,
            )
            return

        message = reference.resolved

        if not isinstance(message, discord.Message):
            if reference.message_id is None:
                await ctx.reply(
                    "I couldn't resolve the referenced message.",
                    mention_author=False,
                )
                return

            try:
                message = await ctx.channel.fetch_message(reference.message_id)
            except discord.HTTPException:
                logger.exception("[STEAL] Failed to fetch replied-to message")
                await ctx.reply(
                    "I couldn't retrieve the referenced message.",
                    mention_author=False,
                )
                return

        await self.show_results(
            items=get_message_items(message),
            owner_id=ctx.author.id,
            ctx=ctx,
        )

    @app_commands.command(
        name="steal",
        description="Extract custom emojis and stickers from a message.",
    )
    @app_commands.describe(message_id="ID of a message in this channel")
    @app_commands.guild_only()
    async def steal_slash(
        self,
        interaction: discord.Interaction,
        message_id: str,
    ) -> None:
        channel = interaction.channel

        logger.info(
            "[STEAL] Slash command | user=%s | channel=%s | message_id=%r",
            interaction.user,
            getattr(channel, "id", None),
            message_id,
        )

        if channel is None or not hasattr(channel, "fetch_message"):
            await interaction.response.send_message(
                "I can't fetch messages from this channel.",
                ephemeral=True,
            )
            return

        try:
            target_id = int(message_id)

            if target_id <= 0:
                raise ValueError

        except ValueError:
            await interaction.response.send_message(
                "Please provide a valid message ID.",
                ephemeral=True,
            )
            return

        try:
            message = await channel.fetch_message(target_id)
        except discord.HTTPException:
            logger.exception("[STEAL] Failed to fetch slash-command target")
            await interaction.response.send_message(
                "I couldn't retrieve that message from this channel.",
                ephemeral=True,
            )
            return

        await self.show_results(
            items=get_message_items(message),
            owner_id=interaction.user.id,
            interaction=interaction,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(StealEmoji(bot))
