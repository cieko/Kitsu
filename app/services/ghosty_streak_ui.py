"""A compact Components V2 panel for previous attempts and the current streak."""

from __future__ import annotations

import asyncio
from io import BytesIO
import logging
from typing import Any

import discord

from app.services.ghosty_streak_service import GhostyStreakService, StreakSnapshot, render_streak_chart


logger = logging.getLogger(__name__)
CHART_FILENAME = "ghosty-streak.png"


def format_selected_forecast(prediction: dict[str, Any]) -> str:
    """Show one selected attempt and its saved chance, including a final miss."""
    ready = bool(prediction.get("ready"))
    hit_at = prediction.get("hit_at")
    percent = prediction.get("candidate_percent")
    if percent is None:
        percent = prediction.get("hit_percent")
    target = f"{hit_at:,}" if ready and hit_at is not None else "Still learning"
    chance = "Still learning"
    if ready and percent is not None:
        chance = "<0.1%" if 0 < percent < 0.1 else f"{percent:.1f}".rstrip("0").rstrip(".") + "%"
    missed = ready and hit_at is not None and prediction.get("estimate_status") == "missed"
    if missed:
        target = f"was {target} MISSED"
    verb = "was" if missed else "is"
    return f"**Estimated hit @** `{target}`\n**Next-gamble chance** {verb} `{chance}`"


class GhostyStreakView(discord.ui.LayoutView):
    def __init__(
        self, *, service: GhostyStreakService, snapshot: StreakSnapshot,
        owner_id: int, guild_id: int, channel_id: int, prefix: str,
        can_attach_files: bool = True,
    ):
        super().__init__(timeout=300)
        self.service, self.snapshot = service, snapshot
        self.owner_id, self.guild_id, self.channel_id = owner_id, guild_id, channel_id
        self.prefix, self.can_attach_files = prefix, can_attach_files
        self.mode = "previous"
        self.message: discord.Message | None = None
        self._chart: bytes | None = None
        self._lock = asyncio.Lock()
        self._expired = False
        self._build()

    async def chart_file(self) -> discord.File | None:
        if self.mode != "current":
            return None
        if self._chart is None:
            self._chart = await asyncio.to_thread(render_streak_chart, self.snapshot)
        return discord.File(BytesIO(self._chart), filename=CHART_FILENAME)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self._expired:
            text = f"This panel expired. Run `{self.prefix}ghosty streak` again."
        elif interaction.user.id != self.owner_id:
            text = f"Run `{self.prefix}ghosty streak` to open your own channel overview."
        elif (
            interaction.guild is None or interaction.guild.id != self.guild_id
            or interaction.channel_id != self.channel_id
        ):
            text = "Use this panel in its original channel."
        else:
            return True
        await interaction.response.send_message(text, ephemeral=True)
        return False

    def _build(self) -> None:
        self.clear_items()
        menu = discord.ui.Select(
            placeholder="Choose a view…", custom_id="ghosty:streak:view",
            options=[
                discord.SelectOption(label=label, value=value, default=self.mode == value)
                for label, value in (("Previous Attempts", "previous"), ("Current Streak", "current"))
            ],
        )

        async def choose(interaction: discord.Interaction) -> None:
            await self._update(interaction, mode=menu.values[0])

        menu.callback = choose
        refresh = discord.ui.Button(label="Refresh", custom_id="ghosty:streak:refresh", style=discord.ButtonStyle.primary)

        async def reload_data(interaction: discord.Interaction) -> None:
            await self._update(interaction, refresh=True)

        refresh.callback = reload_data
        menu.disabled = refresh.disabled = self._expired
        items = [discord.ui.TextDisplay(self._previous() if self.mode == "previous" else self._current())]
        if self.mode == "current":
            items.append(discord.ui.MediaGallery(discord.MediaGalleryItem(
                f"attachment://{CHART_FILENAME}", description="Saved possible hit attempts for the current streak",
            )))
        items.extend((discord.ui.ActionRow(menu), discord.ui.ActionRow(refresh)))
        self.add_item(discord.ui.Container(*items, accent_colour=0x7CC6B0))

    def _previous(self) -> str:
        attempts = self.snapshot.previous_attempts[:5]
        if not attempts:
            return "**Previous Attempts**\nNo completed streaks recorded yet."
        hits = " · ".join(f"`{row.hit_at:,}`" for row in attempts)
        headings = ("Hit @", "Lost", "Received", "Win/Loss")
        rows = [(f"{row.hit_at:,}", f"{row.loss:,}", f"{row.payout:,}", f"{row.net:+,}") for row in attempts]
        widths = [max(len(heading), *(len(row[index]) for row in rows)) for index, heading in enumerate(headings)]
        table = ["  ".join(value.rjust(width) for value, width in zip(row, widths)) for row in (headings, *rows)]
        return (
            f"**Previous Attempts**\n**Last {len(attempts)} hits @** {hits}\n\n"
            "**Amounts · Goos · latest first**\n```text\n" + "\n".join(table) + "\n```"
        )

    def _current(self) -> str:
        stats = self.snapshot.stats
        return (
            "**Current Streak**\n"
            f"**Streak:** `{int(stats.get('streak', 0)):,}` · "
            f"**Loss:** `{int(stats.get('streak_loss', 0)):,} Goos`\n\n"
            f"{format_selected_forecast(self.snapshot.prediction)}"
        )

    async def _update(
        self, interaction: discord.Interaction, *, mode: str | None = None, refresh: bool = False,
    ) -> None:
        await interaction.response.defer()
        async with self._lock:
            if mode == "current" and not self.can_attach_files:
                await interaction.followup.send("I need **Attach Files** in this channel to show the graph.", ephemeral=True)
                return
            old = self.snapshot, self.mode, self._chart
            try:
                if refresh:
                    self.snapshot = await self.service.load(self.guild_id, self.channel_id)
                    self._chart = None
                elif mode in {"previous", "current"}:
                    self.mode = mode
                chart = await self.chart_file()
                self._build()
                await interaction.edit_original_response(
                    view=self, attachments=[chart] if chart is not None else [],
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except Exception:
                self.snapshot, self.mode, self._chart = old
                self._build()
                logger.exception("Could not update Ghosty streak panel for channel %s.", self.channel_id)
                await interaction.followup.send("I couldn’t update this panel. Please try again.", ephemeral=True)

    async def on_timeout(self) -> None:
        self._expired = True
        for item in self.walk_children():
            if isinstance(item, (discord.ui.Select, discord.ui.Button)):
                item.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                logger.debug("Ghosty streak panel expired after its message became unavailable.")
