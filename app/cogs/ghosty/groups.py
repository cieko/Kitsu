from __future__ import annotations

from discord import app_commands

ghosty = app_commands.Group(
    name="ghosty",
    description="Track Ghosty gamble results and channel statistics.",
)


tempchannel = app_commands.Group(
    name="tempchannel",
    description="Manage your own Ghosty temporary channel.",
)


tempchannels = app_commands.Group(
    name="tempchannels",
    description="Staff management for Ghosty temporary channels.",
)


ghosty.add_command(tempchannel)


ghosty.add_command(tempchannels)
