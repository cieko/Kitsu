from __future__ import annotations

from app.bot.permissions import GuildPermissionGroup

translation = GuildPermissionGroup(
    name="translation",
    description="Configure automatic message translations.",
    permission="manage_guild",
    permission_label="Manage Server",
)
