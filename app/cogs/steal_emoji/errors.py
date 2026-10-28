from __future__ import annotations

import asyncio

import aiohttp
import discord

from .constants import logger
from .models import StealItem


def upload_error_message(error: Exception, kind: str, *, upload_started: bool) -> str:
    """Translate upload failures into a useful reply without exposing API details."""
    if isinstance(error, discord.RateLimited):
        return "Discord is receiving too many requests. Please wait a little before trying again."
    if isinstance(error, (aiohttp.ClientError, asyncio.TimeoutError)):
        if upload_started:
            return (
                f"Discord stopped responding while adding this {kind}. "
                f"Check the server's {'stickers' if kind == 'sticker' else 'emojis'} before trying again."
            )
        return f"I couldn't download this {kind} right now. Please try again in a moment."

    code = getattr(error, "code", 0)
    status = getattr(error, "status", 0)
    if code == 30039:
        return (
            "This server is out of sticker slots. "
            "Ask an admin to remove an unused sticker, then try again."
        )
    if code in {30008, 30018, 30056}:
        label = "animated emoji" if code == 30018 else "emoji"
        return (
            f"This server is out of {label} slots. "
            "Ask an admin to remove an unused emoji of this type, then try again."
        )
    if status == 403 or code in {50001, 50013}:
        return (
            f"I don't have permission to add this {kind}. "
            "Ask an admin to check my Create Expressions permission."
        )
    if status == 413 or code in {40005, 50045, 50138}:
        return f"This {kind} file is too large for Discord. Please try a smaller one."
    if code == 50124:
        return "This sticker animation is too long. Please try one that's five seconds or shorter."
    if code in {50035, 50046, 50081, 50109, 50110, 50123}:
        return f"Discord couldn't use this {kind} file. Please try a different {kind}."
    if status == 429:
        return "Discord is receiving too many requests. Please wait a little before trying again."
    if status >= 500:
        return (
            f"Discord is having trouble adding this {kind} right now. Please try again in a moment."
        )
    return (
        f"I couldn't add this {kind}. Ask an admin to check the server's permissions "
        f"and available {kind} slots, then try again."
    )


async def send_upload_reply(
    interaction: discord.Interaction,
    item: StealItem,
    message: str,
    *,
    added: bool = False,
) -> None:
    """A failed confirmation must not turn a successful upload into another attempt."""
    try:
        await interaction.followup.send(message, ephemeral=True)
    except (
        discord.HTTPException,
        discord.RateLimited,
        aiohttp.ClientError,
        asyncio.TimeoutError,
    ) as error:
        logger.warning(
            "[STEAL] Could not deliver upload reply | item_id=%s added=%s status=%s code=%s",
            item.item_id,
            added,
            getattr(error, "status", None),
            getattr(error, "code", None),
        )
