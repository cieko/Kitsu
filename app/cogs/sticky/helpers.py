from __future__ import annotations

import discord


def get_channel_name(guild: discord.Guild, channel_id: int) -> str:
    channel = guild.get_channel(channel_id)

    if isinstance(channel, discord.TextChannel):
        return f"#{channel.name}"

    return str(channel_id)


def get_sticky_preview(sticky: dict, limit: int = 180) -> str:
    script = " ".join(sticky.get("message_script", "").split())

    if not script:
        return "*Empty message*"

    if len(script) > limit:
        script = script[: limit - 3] + "..."

    return script


def build_sticky_options(
    guild: discord.Guild,
    stickies: list[dict],
) -> list[discord.SelectOption]:
    return [
        discord.SelectOption(
            label=get_channel_name(guild, sticky["channel_id"])[:100],
            value=str(sticky["channel_id"]),
            description=("Ignore bots: " + ("ON" if sticky.get("ignore_bots", True) else "OFF")),
        )
        for sticky in stickies[:25]
    ]
