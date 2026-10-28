from __future__ import annotations

import time
from typing import Any

import discord

from app.database.repositories import ghosty_repository as repository

from .constants import DASHBOARD_REFRESH_SECONDS, DEFAULT_MINIMUM_SAMPLES


class GhostyDashboard:
    async def _build_dashboard_view(
        self,
        guild: discord.Guild,
        page: int | None = None,
    ) -> discord.ui.LayoutView:
        """Build the paginated Ghosty dashboard using Components V2."""
        stats_documents = await repository.get_all_channel_stats(guild.id)

        excluded_channels = {
            int(channel_id)
            for channel_id in await repository.get_config(
                guild.id,
                "dashboard_excluded_channels",
                [],
            )
        }

        stats_documents = [
            stats
            for stats in stats_documents
            if int(stats["channel_id"]) not in excluded_channels
            and int(stats.get("total_gambles", 0)) > 0
            and guild.get_channel(int(stats["channel_id"])) is not None
        ]

        minimum_samples = int(
            await repository.get_config(
                guild.id,
                "minimum_samples",
                DEFAULT_MINIMUM_SAMPLES,
            )
        )

        rows: list[dict[str, Any]] = []

        for stats in stats_documents:
            channel_id = int(stats["channel_id"])

            prediction = await self.service.get_prediction_data(
                guild.id,
                channel_id,
                minimum_samples=minimum_samples,
                stats=stats,
            )

            rows.append(
                {
                    "channel_id": channel_id,
                    "streak": int(stats.get("streak", 0)),
                    "loss": int(stats.get("streak_loss", 0)),
                    "hit_at": prediction.get("hit_at"),
                    "hit_percent": prediction.get(
                        "candidate_percent", prediction.get("hit_percent")
                    ),
                    "ready": bool(prediction.get("ready")),
                    "estimate_status": prediction.get("estimate_status"),
                    "paused": bool(stats.get("pause")),
                }
            )

        rows.sort(
            key=lambda row: (
                not row["ready"],
                (
                    -row["hit_percent"]
                    if row["ready"] and row["hit_percent"] is not None
                    else float("inf")
                ),
                row["channel_id"],
            )
        )

        channels_per_page = 5
        total_pages = max(
            1,
            (len(rows) + channels_per_page - 1) // channels_per_page,
        )

        saved_page = int(
            await repository.get_config(
                guild.id,
                "dashboard_page",
                0,
            )
        )
        if page is None:
            page = saved_page

        page = max(0, min(page, total_pages - 1))

        if page != saved_page:
            await repository.set_config(guild.id, "dashboard_page", page)

        page_rows = rows[page * channels_per_page : (page + 1) * channels_per_page]

        def padded_label(value: str, width: int) -> str:
            value = str(value)

            if len(value) > width:
                value = value[: max(1, width - 1)] + "…"

            left = max(0, (width - len(value)) // 2)
            right = max(0, width - len(value) - left)

            return "\u2007" * left + value + "\u2007" * right

        view = discord.ui.LayoutView(timeout=None)

        # ---------------------------------------------------------
        # Section: Ghosty Market
        # ---------------------------------------------------------

        container = discord.ui.Container(
            accent_colour=discord.Colour.dark_purple(),
        )

        container.add_item(discord.ui.TextDisplay(content="### Ghosty Market"))

        container.add_item(
            discord.ui.TextDisplay(
                content=("-# Channel Name   |   Streak   |   Loss   |   Hit @   |   % Of hitting")
            )
        )

        if not rows:
            container.add_item(
                discord.ui.TextDisplay(
                    content="No confirmed Ghosty gambles have been recorded yet."
                )
            )
        else:
            for row in page_rows:
                channel_id = row["channel_id"]
                channel = guild.get_channel(channel_id)
                channel_name = f"#{channel.name}" if channel else f"#{channel_id}"
                if row["paused"]:
                    channel_name = "🔒" + channel_name
                channel_url = f"https://discord.com/channels/{guild.id}/{channel_id}"
                hit_at = str(row["hit_at"]) if row["ready"] and row["hit_at"] is not None else "-"
                if row["ready"] and row["estimate_status"] == "missed":
                    hit_at += " ×"
                hit_percent = (
                    ("<0.1%" if 0 < row["hit_percent"] < 0.1 else f"{row['hit_percent']:.1f}%")
                    if row["ready"] and row["hit_percent"] is not None
                    else "-"
                )

                row_components = discord.ui.ActionRow()
                row_components.add_item(
                    discord.ui.Button(
                        label=padded_label(channel_name, 9),
                        style=discord.ButtonStyle.link,
                        url=channel_url,
                    )
                )
                for value, width in (
                    (f"{row['streak']:,}", 6),
                    (f"{row['loss']:,}", 7),
                    (hit_at, 7),
                    (hit_percent, 6),
                ):
                    row_components.add_item(
                        discord.ui.Button(
                            label=padded_label(value, width),
                            style=discord.ButtonStyle.secondary,
                            disabled=True,
                        )
                    )
                container.add_item(row_components)

            container.add_item(discord.ui.Separator())
            navigation_row = discord.ui.ActionRow()
            previous_button = discord.ui.Button(
                label="Previous", style=discord.ButtonStyle.secondary, disabled=page == 0
            )
            page_indicator = discord.ui.Button(
                label=f"Page {page + 1} of {total_pages}",
                style=discord.ButtonStyle.secondary,
                disabled=True,
            )
            next_button = discord.ui.Button(
                label="Next",
                style=discord.ButtonStyle.secondary,
                disabled=page >= total_pages - 1,
            )

            async def change_page(interaction: discord.Interaction, target_page: int) -> None:
                new_view = await self._build_dashboard_view(guild, page=target_page)
                await interaction.response.edit_message(view=new_view)

            async def go_previous(interaction: discord.Interaction) -> None:
                await change_page(interaction, page - 1)

            async def go_next(interaction: discord.Interaction) -> None:
                await change_page(interaction, page + 1)

            previous_button.callback = go_previous
            next_button.callback = go_next
            navigation_row.add_item(previous_button)
            navigation_row.add_item(page_indicator)
            navigation_row.add_item(next_button)
            container.add_item(navigation_row)

        container.add_item(
            discord.ui.TextDisplay(
                content=(f"-# zᶻ Refresh <t:{int(time.time()) + DASHBOARD_REFRESH_SECONDS}:R>")
            )
        )

        view.add_item(container)

        return view
