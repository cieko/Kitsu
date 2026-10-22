from __future__ import annotations

import logging
import asyncio
import time
from collections import defaultdict
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands, tasks

from app.database.repositories import ghosty_repository as repository
from app.services.ghosty_service import (
    GHOSTY_BOT_ID,
    GhostyService,
    detect_gamble,
)

logger = logging.getLogger(__name__)

DEFAULT_MINIMUM_SAMPLES = 50
DASHBOARD_REFRESH_SECONDS = 60
BACKFILL_IMPORT_CONFIRMATION = "IMPORT"


class GhostyCog(commands.Cog):
    ghosty = app_commands.Group(
        name="ghosty",
        description="Track Ghosty gamble results and channel statistics.",
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.service = GhostyService(bot)
        self._initialized = False
    
    async def cog_load(self) -> None:
        await self.service.initialize()
        self._initialized = True

        self.bot.add_view(self._build_dashboard_controls_view())

        if not self.refresh_dashboard.is_running():
            self.refresh_dashboard.start()

    def cog_unload(self) -> None:
        if self.refresh_dashboard.is_running():
            self.refresh_dashboard.cancel()

    # ---------------------------------------------------------
    # Permission helpers
    # ---------------------------------------------------------

    async def _require_guild(
        self,
        interaction: discord.Interaction,
    ) -> bool:
        if interaction.guild is None:
            await interaction.response.send_message(
                "Use this command inside a server.",
                ephemeral=True,
            )
            return False

        return True

    async def _require_admin(
        self,
        interaction: discord.Interaction,
    ) -> bool:
        if interaction.guild is None:
            await interaction.response.send_message(
                "Use this command inside a server.",
                ephemeral=True,
            )
            return False

        permissions = interaction.user.guild_permissions

        if not (
            permissions.administrator
            or permissions.manage_guild
        ):
            await interaction.response.send_message(
                "You need Manage Server permission to use this command.",
                ephemeral=True,
            )
            return False

        return True

    async def _get_message(
        self,
        channel: discord.abc.Messageable,
        message_id: int,
    ) -> discord.Message | None:
        try:
            return await channel.fetch_message(message_id)
        except (
            discord.NotFound,
            discord.Forbidden,
            discord.HTTPException,
        ):
            return None

    # ---------------------------------------------------------
    # Dashboard
    # ---------------------------------------------------------

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
            )

            rows.append({
                "channel_id": channel_id,
                "streak": int(stats.get("streak", 0)),
                "loss": int(stats.get("streak_loss", 0)),
                "hit_at": prediction.get("hit_at"),
                "hit_percent": prediction.get("hit_percent"),
                "ready": bool(prediction.get("ready")),
            })

        rows.sort(
            key=lambda row: (
                not row["ready"],
                (
                    row["hit_percent"]
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

        if page is None:
            page = int(
                await repository.get_config(
                    guild.id,
                    "dashboard_page",
                    0,
                )
            )

        page = max(0, min(page, total_pages - 1))

        await repository.set_config(
            guild.id,
            "dashboard_page",
            page,
        )

        page_rows = rows[
            page * channels_per_page:
            (page + 1) * channels_per_page
        ]

        def padded_label(value: str, width: int) -> str:
                    value = str(value)
        
                    if len(value) > width:
                        value = value[:max(1, width - 1)] + "…"
        
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

        container.add_item(
            discord.ui.TextDisplay(content="### Ghosty Market")
        )

        container.add_item(
            discord.ui.TextDisplay(
                content=(
                    "-# Channel Name   |   Streak   |   Loss   |   "
                    "Hit @   |   % Of hitting"
                )
            )
        )

  
        if not rows:
            container.add_item(discord.ui.TextDisplay(
                content="No confirmed Ghosty gambles have been recorded yet."
            ))
        else:
            for row in page_rows:
                channel_id = row["channel_id"]
                channel = guild.get_channel(channel_id)
                channel_name = f"#{channel.name}" if channel else f"#{channel_id}"
                channel_url = f"https://discord.com/channels/{guild.id}/{channel_id}"
                hit_at = str(row["hit_at"]) if row["ready"] and row["hit_at"] is not None else "-"
                hit_percent = f"{row['hit_percent']:.0f}%" if row["ready"] and row["hit_percent"] is not None else "-"

                row_components = discord.ui.ActionRow()
                row_components.add_item(discord.ui.Button(
                    label=padded_label(channel_name, 9),
                    style=discord.ButtonStyle.link,
                    url=channel_url,
                ))
                for value, width in (
                    (f"{row['streak']:,}", 6),
                    (f"{row['loss']:,}", 7),
                    (hit_at, 7),
                    (hit_percent, 5),
                ):
                    row_components.add_item(discord.ui.Button(
                        label=padded_label(value, width),
                        style=discord.ButtonStyle.secondary,
                        disabled=True,
                    ))
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
                label="Next", style=discord.ButtonStyle.secondary,
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
                content=(
                    f"-# zᶻ Refresh "
                    f"<t:{int(time.time()) + DASHBOARD_REFRESH_SECONDS}:R>"
                )
            )
        )

        view.add_item(container)

        return view

    
    def _build_dashboard_controls_view(self) -> discord.ui.LayoutView:
        """Build persistent dashboard controls with interactive settings."""
        view = discord.ui.LayoutView(timeout=None)

        controls = discord.ui.Container(
            accent_colour=discord.Colour.dark_purple(),
        )

        controls.add_item(
            discord.ui.TextDisplay(
                content=(
                    "Track Ghosty gambling activity, explore channel statistics, "
                    "andㅤ\nkeep up with recorded hits and streaks."
                )
            )
        )

        row = discord.ui.ActionRow()

        terms_button = discord.ui.Button(
            label="Terms of Service",
            style=discord.ButtonStyle.secondary,
            custom_id="ghosty:terms",
        )

        policy_button = discord.ui.Button(
            label="Policy",
            style=discord.ButtonStyle.secondary,
            custom_id="ghosty:policy",
        )

        settings_button = discord.ui.Button(
            label="⚙️",
            style=discord.ButtonStyle.secondary,
            custom_id="ghosty:settings",
        )

        async def show_terms(
            interaction: discord.Interaction,
        ) -> None:
            panel = discord.ui.LayoutView(timeout=180)
            container = discord.ui.Container(
                accent_colour=discord.Colour.dark_purple(),
            )

            container.add_item(
                discord.ui.TextDisplay(
                    content=(
                        "## Ghosty Dashboard — Terms of Service\n"
                        "The Ghosty Dashboard is a community statistics tool "
                        "that records detected gambling outcomes."
                    )
                )
            )

            container.add_item(
                discord.ui.TextDisplay(
                    content=(
                        "**1. Informational use**\n"
                        "Statistics and predictions are estimates based on "
                        "recorded historical data. They are not guarantees "
                        "of future outcomes.\n\n"
                        "**2. Data accuracy**\n"
                        "Results depend on accessible messages and successful "
                        "gamble detection. Missing or incorrectly classified "
                        "messages may affect the displayed statistics.\n\n"
                        "**3. No guaranteed wins**\n"
                        "A displayed streak or hit estimate does not guarantee "
                        "that the next gamble will win.\n\n"
                        "**4. Responsible use**\n"
                        "Use the dashboard for informational purposes. "
                        "Do not treat its estimates as a promise of profit."
                    )
                )
            )

            panel.add_item(container)

            await interaction.response.send_message(
                view=panel,
                ephemeral=True,
            )

        async def show_policy(
            interaction: discord.Interaction,
        ) -> None:
            panel = discord.ui.LayoutView(timeout=180)
            container = discord.ui.Container(
                accent_colour=discord.Colour.dark_purple(),
            )

            container.add_item(
                discord.ui.TextDisplay(
                    content="## Ghosty Dashboard — Tracking Policy"
                )
            )

            container.add_item(
                discord.ui.TextDisplay(
                    content=(
                        "**What is recorded?**\n"
                        "Kitsu detects supported Ghosty wager commands and "
                        "corresponding result messages to maintain channel "
                        "statistics.\n\n"
                        "**How are streaks calculated?**\n"
                        "The current streak tracks consecutive recorded misses "
                        "since the latest hit. Historical statistics depend "
                        "on the results available to Kitsu.\n\n"
                        "**What does Hit @ mean?**\n"
                        "It represents the estimated hit position derived "
                        "from recorded historical data and the bot's "
                        "prediction logic.\n\n"
                        "**What does the percentage mean?**\n"
                        "It is the estimate produced by that prediction logic, "
                        "not a guarantee or necessarily the true probability "
                        "of the next gamble.\n\n"
                        "**Channel exclusions**\n"
                        "Excluded channels are hidden from the market display. "
                        "Their historical records are not deleted."
                    )
                )
            )

            panel.add_item(container)

            await interaction.response.send_message(
                view=panel,
                ephemeral=True,
            )

        async def show_settings(
            interaction: discord.Interaction,
        ) -> None:
            guild = interaction.guild

            if guild is None:
                await interaction.response.send_message(
                    "Use these settings inside a server.",
                    ephemeral=True,
                )
                return

            permissions = interaction.user.guild_permissions

            if not (
                permissions.administrator
                or permissions.manage_guild
            ):
                await interaction.response.send_message(
                    "You need Manage Server permission to change "
                    "Ghosty dashboard settings.",
                    ephemeral=True,
                )
                return

            # Only include ordinary text channels that the bot can view.
            bot_member = (
                guild.get_member(self.bot.user.id)
                if self.bot.user is not None
                else None
            )

            channels = [
                channel
                for channel in guild.text_channels
                if (
                    bot_member is None
                    or channel.permissions_for(bot_member).view_channel
                )
            ]

            channels.sort(
                key=lambda channel: (
                    channel.position,
                    channel.name.casefold(),
                )
            )

            accessible_channel_ids = {
                channel.id for channel in channels
            }

            existing_exclusions = await repository.get_config(
                guild.id,
                "dashboard_excluded_channels",
                [],
            )

            # Preserve exclusions for channels that are no longer accessible.
            excluded_channel_ids: set[int] = {
                int(channel_id)
                for channel_id in existing_exclusions
            }

            excluded_channel_ids.intersection_update(
                excluded_channel_ids - accessible_channel_ids
            )

            # Keep the current exclusions for channels in this list.
            excluded_channel_ids.update(
                int(channel_id)
                for channel_id in existing_exclusions
                if int(channel_id) in accessible_channel_ids
            )

            channels_per_page = 10

            total_pages = max(
                1,
                (
                    len(channels) + channels_per_page - 1
                ) // channels_per_page,
            )

            async def build_settings_view(
                page: int,
            ) -> discord.ui.LayoutView:
                page = max(0, min(page, total_pages - 1))

                settings_view = discord.ui.LayoutView(timeout=300)

                settings_container = discord.ui.Container(
                    accent_colour=discord.Colour.dark_purple(),
                )

                settings_container.add_item(
                    discord.ui.TextDisplay(
                        content=(
                            "## Ghosty Market Settings\n"
                            "Toggle each text channel to choose whether it "
                            "appears on the market dashboard.\n\n"
                            "✅ **Included** — shown on the dashboard\n"
                            "🚫 **Excluded** — hidden from the dashboard\n\n"
                            "These settings never delete historical records."
                        )
                    )
                )

                settings_container.add_item(
                    discord.ui.Separator()
                )

                start_index = page * channels_per_page
                page_channels = channels[
                    start_index:start_index + channels_per_page
                ]

                if not channels:
                    settings_container.add_item(
                        discord.ui.TextDisplay(
                            content=(
                                "No accessible text channels were found."
                            )
                        )
                    )

                # Render up to five channel buttons per row.
                for row_start in range(0, len(page_channels), 5):
                    channel_row = discord.ui.ActionRow()

                    for channel in page_channels[
                        row_start:row_start + 5
                    ]:
                        is_excluded = (
                            channel.id in excluded_channel_ids
                        )

                        marker = "🚫" if is_excluded else "✅"
                        label = f"{marker} {channel.name}"[:80]

                        channel_button = discord.ui.Button(
                            label=label,
                            style=(
                                discord.ButtonStyle.danger
                                if is_excluded
                                else discord.ButtonStyle.success
                            ),
                            custom_id=(
                                f"ghosty:settings:toggle:{channel.id}"
                            ),
                        )

                        async def toggle_channel(
                            button_interaction: discord.Interaction,
                            channel_id: int = channel.id,
                            current_page: int = page,
                        ) -> None:
                            if button_interaction.guild_id != guild.id:
                                await button_interaction.response.send_message(
                                    "This settings panel belongs to another server.",
                                    ephemeral=True,
                                )
                                return

                            if channel_id in excluded_channel_ids:
                                excluded_channel_ids.remove(channel_id)
                            else:
                                excluded_channel_ids.add(channel_id)

                            updated_view = await build_settings_view(
                                current_page
                            )

                            await button_interaction.response.edit_message(
                                view=updated_view
                            )

                        channel_button.callback = toggle_channel
                        channel_row.add_item(channel_button)

                    settings_container.add_item(channel_row)

                settings_container.add_item(
                    discord.ui.Separator()
                )

                navigation_row = discord.ui.ActionRow()

                previous_button = discord.ui.Button(
                    label="Previous",
                    style=discord.ButtonStyle.secondary,
                    disabled=(page == 0),
                )

                page_indicator = discord.ui.Button(
                    label=f"Page {page + 1}/{total_pages}",
                    style=discord.ButtonStyle.secondary,
                    disabled=True,
                )

                next_button = discord.ui.Button(
                    label="Next",
                    style=discord.ButtonStyle.secondary,
                    disabled=(page >= total_pages - 1),
                )

                async def go_previous(
                    page_interaction: discord.Interaction,
                ) -> None:
                    updated_view = await build_settings_view(page - 1)
                    await page_interaction.response.edit_message(
                        view=updated_view
                    )

                async def go_next(
                    page_interaction: discord.Interaction,
                ) -> None:
                    updated_view = await build_settings_view(page + 1)
                    await page_interaction.response.edit_message(
                        view=updated_view
                    )

                previous_button.callback = go_previous
                next_button.callback = go_next

                navigation_row.add_item(previous_button)
                navigation_row.add_item(page_indicator)
                navigation_row.add_item(next_button)

                settings_container.add_item(navigation_row)

                actions_row = discord.ui.ActionRow()

                save_button = discord.ui.Button(
                    label="Save changes",
                    style=discord.ButtonStyle.primary,
                )

                restore_button = discord.ui.Button(
                    label="Restore all",
                    style=discord.ButtonStyle.secondary,
                )


                async def save_exclusions(interaction: discord.Interaction) -> None:
                    current_exclusions = {
                        int(channel_id)
                        for channel_id in await repository.get_config(
                            guild.id,
                            "dashboard_excluded_channels",
                            [],
                        )
                    }

                    accessible_channel_ids = {channel.id for channel in channels}

                    # Preserve exclusions for channels that aren't currently accessible.
                    inaccessible_exclusions = current_exclusions - accessible_channel_ids

                    saved_exclusions = inaccessible_exclusions | (
                        excluded_channel_ids & accessible_channel_ids
                    )

                    await repository.set_config(
                        guild.id,
                        "dashboard_excluded_channels",
                        sorted(saved_exclusions),
                    )

                    # Acknowledge the interaction before performing other operations.
                    await interaction.response.defer(ephemeral=True)

                    # Delete the temporary settings panel.
                    try:
                        await interaction.delete_original_response()
                    except (discord.NotFound, discord.HTTPException):
                        pass

                    # Show confirmation immediately after deleting the settings panel.
                    await interaction.followup.send(
                        (
                            "Ghosty Market settings saved.\n"
                            f"**Excluded channels:** {len(saved_exclusions)}\n"
                            "The dashboard will refresh shortly. Historical records have not been changed."
                        ),
                        ephemeral=True,
                    )

                    # Give the confirmation a moment before rebuilding the dashboard.
                    await asyncio.sleep(3)

                    # Delete the old dashboard and controls, then post fresh messages.
                    await self._repost_sticky_dashboard(guild)


                async def restore_all(
                    restore_interaction: discord.Interaction,
                ) -> None:
                    if restore_interaction.guild_id != guild.id:
                        await restore_interaction.response.send_message(
                            "This settings panel belongs to another server.",
                            ephemeral=True,
                        )
                        return

                    excluded_channel_ids.clear()

                    await repository.set_config(
                        guild.id,
                        "dashboard_excluded_channels",
                        [],
                    )

                    updated_view = await build_settings_view(page)

                    await restore_interaction.response.edit_message(
                        view=updated_view
                    )

                    await restore_interaction.followup.send(
                        "All channels have been restored to the Ghosty Market. "
                        "Historical records remain unchanged.",
                        ephemeral=True,
                    )

                save_button.callback = save_exclusions
                restore_button.callback = restore_all

                actions_row.add_item(save_button)
                actions_row.add_item(restore_button)

                settings_container.add_item(actions_row)
                settings_view.add_item(settings_container)

                return settings_view

            settings_view = await build_settings_view(0)

            await interaction.response.send_message(
                view=settings_view,
                ephemeral=True,
            )

        terms_button.callback = show_terms
        policy_button.callback = show_policy
        settings_button.callback = show_settings

        row.add_item(terms_button)
        row.add_item(policy_button)
        row.add_item(settings_button)

        controls.add_item(row)
        view.add_item(controls)

        return view

    
    async def _repost_sticky_dashboard(self, guild: discord.Guild) -> None:
        """Repost both Ghosty dashboard messages together."""

        # logger.warning(
        #     "GHOSTY DASHBOARD REPOST TRIGGERED for guild %s",
        #     guild.id,
        #     stack_info=True,
        # )
        
        channel_id = await repository.get_config(
            guild.id,
            "dashboard_channel_id",
        )

        old_market_id = await repository.get_config(
            guild.id,
            "dashboard_message_id",
        )

        old_controls_id = await repository.get_config(
            guild.id,
            "dashboard_controls_message_id",
        )

        old_tracker_id = await repository.get_config(
            guild.id,
            "dashboard_tracker_message_id",
        )

        if not channel_id:
            return

        channel = guild.get_channel(int(channel_id))

        if not isinstance(channel, discord.TextChannel):
            return

        old_market = (
            await self._get_message(channel, int(old_market_id))
            if old_market_id
            else None
        )

        old_controls = (
            await self._get_message(channel, int(old_controls_id))
            if old_controls_id
            else None
        )

        old_tracker = (
            await self._get_message(channel, int(old_tracker_id))
            if old_tracker_id
            else None
        )

        try:
            tracker_message = await channel.send(
                "## @ t r a c k e r"
            )

            market_view = await self._build_dashboard_view(guild)
            controls_view = self._build_dashboard_controls_view()

            # Send the market first, then the controls message.
            new_market = await channel.send(view=market_view)
            new_controls = await channel.send(view=controls_view)

        except discord.HTTPException:
            logger.exception(
                "Failed to repost Ghosty dashboard pair in guild %s.",
                guild.id,
            )
            return

        await repository.set_config(
            guild.id,
            "dashboard_message_id",
            str(new_market.id),
        )

        await repository.set_config(
            guild.id,
            "dashboard_controls_message_id",
            str(new_controls.id),
        )

        await repository.set_config(
            guild.id,
            "dashboard_tracker_message_id",
            str(tracker_message.id),
        )

        # Delete old messages only after both replacements were sent.
        for old_message in (old_tracker, old_market, old_controls):
            if old_message is None:
                continue

            try:
                await old_message.delete()
            except (
                discord.NotFound,
                discord.Forbidden,
                discord.HTTPException,
            ):
                logger.warning(
                    "Could not delete old Ghosty dashboard message %s.",
                    old_message.id,
                )
    
    
    async def _refresh_dashboard_for_guild(
        self,
        guild: discord.Guild,
    ) -> None:
        channel_id = await repository.get_config(
            guild.id,
            "dashboard_channel_id",
        )

        if not channel_id:
            return

        channel = guild.get_channel(int(channel_id))

        if not isinstance(channel, discord.TextChannel):
            return

        market_id = await repository.get_config(
            guild.id,
            "dashboard_message_id",
        )

        controls_id = await repository.get_config(
            guild.id,
            "dashboard_controls_message_id",
        )

        tracker_id = await repository.get_config(
            guild.id,
            "dashboard_tracker_message_id",
        )

        market_message = (
            await self._get_message(channel, int(market_id))
            if market_id
            else None
        )

        controls_message = (
            await self._get_message(channel, int(controls_id))
            if controls_id
            else None
        )

        tracker_message = (
            await self._get_message(channel, int(tracker_id))
            if tracker_id
            else None
        )

        # Recreate the pair if either message is missing or is not V2.
        pair_is_valid = (
            tracker_message is not None
            and market_message is not None
            and controls_message is not None
            and bool(market_message.flags.components_v2)
            and bool(controls_message.flags.components_v2)
        )

        # logger.info(
        #     "Ghosty dashboard refresh: guild=%s, market_found=%s, "
        #     "controls_found=%s, market_v2=%s, controls_v2=%s",
        #     guild.id,
        #     market_message is not None,
        #     controls_message is not None,
        #     market_message.flags.components_v2 if market_message else None,
        #     controls_message.flags.components_v2 if controls_message else None,
        # )

        if not pair_is_valid:
            market_view = await self._build_dashboard_view(guild)
            controls_view = self._build_dashboard_controls_view()

            try:
                new_tracker = await channel.send(
                    "## @ t r a c k e r"
                )
                new_market = await channel.send(view=market_view)
                new_controls = await channel.send(view=controls_view)
            except discord.HTTPException:
                logger.exception(
                    "Failed to create Ghosty dashboard pair for guild %s.",
                    guild.id,
                )
                return

            await repository.set_config(
                guild.id,
                "dashboard_message_id",
                str(new_market.id),
            )

            await repository.set_config(
                guild.id,
                "dashboard_controls_message_id",
                str(new_controls.id),
            )

            await repository.set_config(
                guild.id,
                "dashboard_tracker_message_id",
                str(new_tracker.id),
            )

            # Remove the old pair after both replacements have been sent.
            for old_message in (tracker_message, market_message, controls_message):
                if old_message is None:
                    continue

                try:
                    await old_message.delete()
                except (
                    discord.NotFound,
                    discord.Forbidden,
                    discord.HTTPException,
                ):
                    logger.warning(
                        "Could not delete old Ghosty dashboard message %s.",
                        old_message.id,
                    )

            return

        # Both messages exist: refresh the market and maintain the controls.
        market_view = await self._build_dashboard_view(guild)
        controls_view = self._build_dashboard_controls_view()

        try:
            await market_message.edit(
                view=market_view,
                content=None,
                embeds=[],
            )

            await controls_message.edit(
                view=controls_view,
                content=None,
                embeds=[],
            )
        except discord.HTTPException:
            logger.exception(
                "Failed to refresh Ghosty dashboard pair for guild %s.",
                guild.id,
            )
    
    @tasks.loop(seconds=DASHBOARD_REFRESH_SECONDS)
    async def refresh_dashboard(self) -> None:
        for guild in self.bot.guilds:
            try:
                await self._refresh_dashboard_for_guild(guild)
            except Exception:
                logger.exception(
                    "Failed to refresh Ghosty dashboard for guild %s.",
                    guild.id,
                )

    @refresh_dashboard.before_loop
    async def before_dashboard_refresh(self) -> None:
        await self.bot.wait_until_ready()

    # ---------------------------------------------------------
    # Live result listener
    # ---------------------------------------------------------

    async def _get_streak_participant_ids(
        self,
        guild_id: int,
        channel_id: int,
        result_message_id: int,
        hit_user_id: int,
    ) -> list[int]:
        """Collect losing users from the recorded streak that just ended."""
        history = await repository.get_gamble_history(
            guild_id,
            channel_id,
            limit=None,
        )
        participant_ids: set[int] = set()
        found_current_hit = False

        for entry in reversed(history):
            if not found_current_hit:
                if str(entry.get("result_message_id")) == str(result_message_id):
                    found_current_hit = True
                continue

            # Stop at the previous hit: only the consecutive misses before the
            # current hit belong to the streak being announced.
            if entry.get("outcome") == "hit":
                break
            if entry.get("outcome") == "miss":
                user_id = int(entry.get("user_id", 0))
                if user_id > 0:
                    participant_ids.add(user_id)

        participant_ids.discard(hit_user_id)
        return sorted(participant_ids)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None:
            return

        # Ignore our own messages; hit notifications explicitly reposition the dashboard.
        if self.bot.user is not None and message.author.id == self.bot.user.id:
            return

        guild = message.guild
        dashboard_channel_id = await repository.get_config(
            guild.id, "dashboard_channel_id"
        )
        dashboard_channel_id_int = (
            int(dashboard_channel_id)
            if dashboard_channel_id and str(dashboard_channel_id).isdigit()
            else None
        )
        dashboard_repositioned = False

        try:
            if message.author.id == GHOSTY_BOT_ID:
                recorded = await self.service.process_message(message)
                if recorded is not None:
                    guild_id = int(recorded["guild_id"])
                    source_channel_id = int(recorded["channel_id"])
                    source_channel = guild.get_channel(source_channel_id)
                    outcome = recorded["outcome"]

                    if outcome == "hit":
                        notification_channel = (
                            guild.get_channel(dashboard_channel_id_int)
                            if dashboard_channel_id_int is not None
                            else None
                        )
                        if not isinstance(notification_channel, discord.TextChannel):
                            notification_channel = source_channel

                        hit_user_id = int(recorded.get("user_id", 0))
                        participant_ids = await self._get_streak_participant_ids(
                            guild_id,
                            source_channel_id,
                            int(recorded["result_message_id"]),
                            hit_user_id,
                        )

                        if isinstance(notification_channel, discord.TextChannel):
                            channel_label = (
                                source_channel.mention
                                if source_channel is not None
                                else f"<#{source_channel_id}>"
                            )
                            participant_mentions = " ".join(
                                f"<@{user_id}>" for user_id in participant_ids
                            )
                            first_line = f"{channel_label} {participant_mentions}".rstrip()
                            stats = recorded["stats"]
                            net_pl = int(stats.get("streak_net_pl", 0))
                            net_label = "profit" if net_pl >= 0 else "loss"
                            net_amount = f"{abs(net_pl):,} Goos"
                            stats = recorded["stats"]

                            last_streak = int(stats.get("last_streak", 0))
                            net_pl = int(stats.get("streak_net_pl", 0))

                            net_label = "profit" if net_pl >= 0 else "loss"
                            net_amount = f"{abs(net_pl):,} Goos"

                            stats_line = (
                                f"Total streak: {last_streak} ・ "
                                f"Total {net_label}: {net_amount}"
                            )
                            
                            content = (
                                f"{first_line}\n"
                                f"🎉  **A Ghosty hit has been recorded!** "
                                f"{('Kudos to <@' + str(hit_user_id) + '>') if hit_user_id > 0 else 'A winning hit was detected'}\n"
                                f"{stats_line}\n"
                                f"-# [Jump to message]({message.jump_url})"
                            )
                            allowed_mentions = discord.AllowedMentions(
                                users=True, roles=False, everyone=False, replied_user=False
                            )
                            await notification_channel.send(
                                content,
                                allowed_mentions=allowed_mentions,
                            )

                            # Send a second, playful announcement in the channel
                            # where the gamble happened (unless it's already the
                            # dashboard notification channel).
                            if (
                                isinstance(source_channel, discord.TextChannel)
                                and source_channel.id != notification_channel.id
                            ):
                                await source_channel.send(
                                    "🎉 Hurrah, a new hit! Ghosty says, "
                                    "you're welcome. 😎\n"
                                    f"{stats_line}",
                                    allowed_mentions=discord.AllowedMentions.none(),
                                )

                            # await self._repost_sticky_dashboard(guild)
                            await self._refresh_dashboard_for_guild(guild)
                            dashboard_repositioned = (
                                notification_channel.id == dashboard_channel_id_int
                            )
                    else:
                        mode = await repository.get_config(
                            guild_id, f"notify:{source_channel_id}", "hit_only"
                        )
                        if mode == "always" and isinstance(source_channel, discord.TextChannel):
                            stats = recorded["stats"]
                            await source_channel.send(
                                f"Streak: {int(stats.get('streak', 0)):,} ・ "
                                f"Total Loss: {int(stats.get('streak_loss', 0)):,} Goos",
                                allowed_mentions=discord.AllowedMentions.none(),
                            )
        except Exception:
            logger.exception(
                "Ghosty message processing failed for message %s.", message.id
            )

        # Any human or other bot message in the dashboard channel moves the dashboard
        # back to the bottom. Messages elsewhere do not cause a repost.
        if (
            not dashboard_repositioned
            and dashboard_channel_id_int is not None
            and message.channel.id == dashboard_channel_id_int
        ):
            try:
                # await self._repost_sticky_dashboard(guild)
                await self._refresh_dashboard_for_guild(guild)
            except Exception:
                logger.exception(
                    "Failed to reposition Ghosty dashboard after message %s.",
                    message.id,
                )

    # ---------------------------------------------------------
    # /ghosty setup
    # ---------------------------------------------------------

    
    @ghosty.command(
        name="setup",
        description="Choose the channel for the Ghosty dashboard.",
    )
    @app_commands.default_permissions(manage_guild=True)
    async def setup_dashboard(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
    ) -> None:
        if not await self._require_admin(interaction):
            return

        guild = interaction.guild
        if guild is None:
            return

        await interaction.response.defer(
            ephemeral=True,
            thinking=True,
        )

        old_channel_id = await repository.get_config(
            guild.id,
            "dashboard_channel_id",
        )

        old_market_id = await repository.get_config(
            guild.id,
            "dashboard_message_id",
        )

        old_controls_id = await repository.get_config(
            guild.id,
            "dashboard_controls_message_id",
        )

        old_tracker_id = await repository.get_config(
            guild.id,
            "dashboard_tracker_message_id",
        )

        # Clean up both messages from the previous setup.
        if old_channel_id:
            old_channel = guild.get_channel(int(old_channel_id))

            if isinstance(old_channel, discord.TextChannel):
                for old_id in (old_tracker_id, old_market_id, old_controls_id):
                    if not old_id:
                        continue

                    old_message = await self._get_message(
                        old_channel,
                        int(old_id),
                    )

                    if old_message is None:
                        continue

                    try:
                        await old_message.delete()
                    except (
                        discord.NotFound,
                        discord.Forbidden,
                        discord.HTTPException,
                    ):
                        logger.warning(
                            "Could not delete previous Ghosty dashboard "
                            "message %s.",
                            old_message.id,
                        )

        await repository.set_config(
            guild.id,
            "dashboard_channel_id",
            str(channel.id),
        )

        try:
            tracker_message = await channel.send(
                "## @ t r a c k e r"
            )

            market_view = await self._build_dashboard_view(guild)
            controls_view = self._build_dashboard_controls_view()

            # Always send the market message first.
            market_message = await channel.send(view=market_view)
            controls_message = await channel.send(view=controls_view)

        except discord.HTTPException:
            logger.exception(
                "Failed to set up Ghosty dashboard pair in guild %s.",
                guild.id,
            )

            await interaction.followup.send(
                "I couldn't post both dashboard messages there. Check that "
                "I have View Channel, Send Messages, and Read Message History "
                "permissions. Run `/ghosty setup` again after fixing permissions.",
                ephemeral=True,
            )
            return

        await repository.set_config(
            guild.id,
            "dashboard_message_id",
            str(market_message.id),
        )

        await repository.set_config(
            guild.id,
            "dashboard_controls_message_id",
            str(controls_message.id),
        )

        await repository.set_config(
            guild.id,
            "dashboard_controls_message_id",
            str(controls_message.id),
        )

        await interaction.followup.send(
            f"Ghosty dashboard and controls configured in {channel.mention}.",
            ephemeral=True,
        )
        

    # ---------------------------------------------------------
    # /ghosty test
    # ---------------------------------------------------------

    @ghosty.command(
        name="test",
        description="Inspect a gamble message without recording it.",
    )
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(
        message_id="ID of the original gamble command message.",
    )
    async def test_gamble(
        self,
        interaction: discord.Interaction,
        message_id: str,
    ) -> None:
        if not await self._require_admin(interaction):
            return

        if not message_id.isdigit():
            await interaction.response.send_message(
                "Message ID must contain digits only.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(
            ephemeral=True,
            thinking=True,
        )

        source = await self._get_message(
            interaction.channel,
            int(message_id),
        )

        if source is None:
            await interaction.followup.send(
                "I couldn't fetch that message. Check the ID, channel, "
                "and my message-history permissions.",
                ephemeral=True,
            )
            return

        wager = detect_gamble(source)

        if wager is None:
            await interaction.followup.send(
                "This message doesn't match a supported Ghosty wager command.",
                ephemeral=True,
            )
            return

        matched_result = None

        try:
            async for candidate in interaction.channel.history(
                limit=1000,
                after=source,
                oldest_first=True,
            ):
                if candidate.author.id != GHOSTY_BOT_ID:
                    continue

                result = await self.service.analyze_result_message(candidate, source)
                if result is not None:
                    matched_result = (result, candidate)
                    break

        except (discord.Forbidden, discord.HTTPException):
            await interaction.followup.send(
                f"Detected wager command: **{wager:,} Goos**.\n"
                "I couldn't scan the messages after it. Check my View Channel "
                "and Read Message History permissions.",
                ephemeral=True,
            )
            return

        if matched_result is None:
            await interaction.followup.send(
                f"Detected wager command: **{wager:,} Goos**.\n"
                "No valid Ghosty gambling response was found after this message. "
                "Other Ghosty messages, including balance replies, are ignored. "
                "No stats were changed.",
                ephemeral=True,
            )
            return

        result, response = matched_result
        await interaction.followup.send(
            f"**Response wager:** {result['wager']:,} Goos\n"
            f"**Ghosty message:** [Jump to message]({response.jump_url})\n"
            f"**Classification:** {result['outcome'].upper()}\n"
            f"**Payout:** {result['payout']:,} Goos\n"
            "**Database:** unchanged",
            ephemeral=True,
        )

    # ---------------------------------------------------------
    # /ghosty config
    # ---------------------------------------------------------

    @ghosty.command(
        name="config",
        description="Configure the minimum history required for predictions.",
    )
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(
        minimum_samples="Minimum recorded gambles per channel (1–100000).",
    )
    async def configure(
        self,
        interaction: discord.Interaction,
        minimum_samples: app_commands.Range[int, 1, 100000],
    ) -> None:
        if not await self._require_admin(interaction):
            return

        await repository.set_config(
            interaction.guild.id,
            "minimum_samples",
            int(minimum_samples),
        )

        await interaction.response.send_message(
            f"Prediction threshold set to **{minimum_samples} gambles "
            "per channel**.",
            ephemeral=True,
        )

    # ---------------------------------------------------------
    # /ghosty start
    # ---------------------------------------------------------

    @ghosty.command(
        name="start",
        description="Enable live Ghosty tracking in this server.",
    )
    @app_commands.default_permissions(manage_guild=True)
    async def start_tracking(
        self,
        interaction: discord.Interaction,
    ) -> None:
        if not await self._require_admin(interaction):
            return

        await self.service.set_live_enabled(
            interaction.guild.id,
            True,
        )

        await interaction.response.send_message(
            "Live Ghosty tracking is now enabled. Kitsu will attempt to "
            "match ordinary Ghosty result messages to preceding gamble "
            "commands. Historical backfill is separate.",
            ephemeral=True,
        )

    # ---------------------------------------------------------
    # /ghosty notify
    # ---------------------------------------------------------

    @ghosty.command(
        name="notify",
        description="Choose whether this channel displays missed gambles.",
    )
    @app_commands.describe(
        mode="always shows misses; hit_only announces hits only.",
    )
    @app_commands.choices(
        mode=[
            app_commands.Choice(name="always", value="always"),
            app_commands.Choice(name="hit_only", value="hit_only"),
        ]
    )
    async def notify_mode(
        self,
        interaction: discord.Interaction,
        mode: app_commands.Choice[str],
    ) -> None:
        if not await self._require_guild(interaction):
            return

        await repository.set_config(
            interaction.guild.id,
            f"notify:{interaction.channel_id}",
            mode.value,
        )

        await interaction.response.send_message(
            f"Ghosty notifications for this channel are now **{mode.value}**.",
            ephemeral=True,
        )

    # ---------------------------------------------------------
    # /ghosty stats
    # ---------------------------------------------------------

    @ghosty.command(
        name="stats",
        description="Show combined Ghosty statistics for this channel.",
    )
    async def show_stats(
        self,
        interaction: discord.Interaction,
    ) -> None:
        if not await self._require_guild(interaction):
            return

        stats = await self.service.get_stats(
            interaction.guild.id,
            interaction.channel_id,
        )

        net_pl = stats["net_pl"]

        await interaction.response.send_message(
            "**Ghosty Channel Stats**\n"
            f'Streak: **{stats["streak"]}**\n'
            f'Current Streak Loss: **{stats["streak_loss"]:,} Goos**\n'
            f'Cumulative Net P/L: **{net_pl:+,} Goos**\n'
            f'Hits: **{stats["hits"]:,}** ・ '
            f'Misses: **{stats["misses"]:,}** ・ '
            f'Total: **{stats["total_gambles"]:,}**',
            ephemeral=True,
        )

    # ---------------------------------------------------------
    # /ghosty backfill
    # ---------------------------------------------------------


    @ghosty.command(
        name="backfill",
        description="Scan accessible channel history for Ghosty gambling results.",
    )
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(
        import_results="Enable importing validated results. False means dry run.",
        confirmation="Type IMPORT when import_results is enabled.",
    )
    async def backfill(
        self,
        interaction: discord.Interaction,
        import_results: bool = False,
        confirmation: str | None = None,
    ) -> None:
        if not await self._require_admin(interaction):
            return

        guild = interaction.guild
        if guild is None:
            return

        # Never mix a historical import with live records or active tracking.
        if import_results:
            if await self.service.is_live_enabled(guild.id):
                await interaction.response.send_message(
                    "Backfill import is blocked while live tracking is enabled. "
                    "Keep live tracking disabled until historical import is complete.",
                    ephemeral=True,
                )
                return
            if await repository.has_live_gambles(guild.id):
                await interaction.response.send_message(
                    "Backfill import is blocked because live results already "
                    "exist in the database. No records were changed.",
                    ephemeral=True,
                )
                return
            if confirmation != BACKFILL_IMPORT_CONFIRMATION:
                await interaction.response.send_message(
                    "To import results, set import_results to True and "
                    "confirmation to IMPORT. Otherwise, run a dry run.",
                    ephemeral=True,
                )
                return

        await interaction.response.defer(ephemeral=True, thinking=True)

        counts = defaultdict(int)
        scanned = 0
        channels_completed = 0
        inaccessible = 0
        total_wagers_detected = 0
        total_ghosty_messages = 0

        for channel in guild.text_channels:
            pending_gambles: list[discord.Message] = []
            channel_scanned = 0
            channel_wagers = 0
            channel_ghosty = 0

            try:
                async for message in channel.history(limit=None, oldest_first=True):
                    scanned += 1
                    channel_scanned += 1

                    wager = detect_gamble(message)
                    if wager is not None:
                        channel_wagers += 1
                        pending_gambles.append(message)
                        continue

                    if message.author.id != GHOSTY_BOT_ID:
                        continue

                    channel_ghosty += 1
                    result = await self.service.analyze_result_message(
                        message,
                        pending_gambles[-1] if pending_gambles else None,
                    )
                    if result is None:
                        counts["ignored"] += 1
                        continue

                    outcome = result["outcome"]
                    counts[outcome] += 1

                    # Compare the actual Ghosty response ID with existing history.
                    # This is a read-only duplicate check.
                    existing_record = await repository.get_gamble_by_result_message(
                        guild.id,
                        message.id,
                    )

                    if existing_record is not None:
                        counts["duplicates"] += 1
                    else:
                        counts["new_candidates"] += 1

                    # Pending commands are attribution candidates only.
                    pending_gambles.clear()

                    logger.info(
                        "Ghosty backfill response classified: guild=%s channel=%s "
                        "response_id=%s outcome=%s wager=%s payout=%s user_id=%s",
                        guild.id,
                        channel.id,
                        message.id,
                        outcome,
                        result["wager"],
                        result["payout"],
                        result["user_id"],
                    )

                    if import_results:
                        recorded = await self.service.record_result(
                            result,
                            source="backfill",
                        )
                        if recorded is None:
                            counts["duplicates"] += 1
                        else:
                            counts["imported"] += 1

                # Commands are not required to have a one-to-one Ghosty reply.
                # Keep a count for visibility, but don't call them unmatched.
                channels_completed += 1
                total_wagers_detected += channel_wagers
                total_ghosty_messages += channel_ghosty
                logger.info(
                    "Ghosty backfill channel summary: channel=%s (%s) "
                    "scanned=%s wagers=%s ghosty_messages=%s hits=%s misses=%s ignored=%s",
                    channel.name,
                    channel.id,
                    channel_scanned,
                    channel_wagers,
                    channel_ghosty,
                    counts["hit"],
                    counts["miss"],
                    counts["ignored"],
                )

            except (discord.Forbidden, discord.HTTPException) as exc:
                inaccessible += 1
                logger.warning(
                    "Ghosty backfill skipped channel=%s (%s): %s",
                    channel.name,
                    channel.id,
                    getattr(exc, "status", "Missing Access"),
                )
                total_wagers_detected += channel_wagers
                total_ghosty_messages += channel_ghosty

        mode = "IMPORT" if import_results else "DRY RUN"
        logger.info(
            "Ghosty backfill final summary: mode=%s guild=%s guild_id=%s "
            "channels_total=%s channels_completed=%s channels_failed=%s "
            "messages_scanned=%s wagers_detected=%s ghosty_messages=%s hits=%s misses=%s "
            "ignored=%s duplicates=%s imported=%s",
            mode,
            guild.name,
            guild.id,
            len(guild.text_channels),
            channels_completed,
            inaccessible,
            scanned,
            total_wagers_detected,
            total_ghosty_messages,
            counts["hit"],
            counts["miss"],
            counts["ignored"],
            counts["duplicates"],
            counts["imported"],
        )

        await interaction.followup.send(
            f"**Ghosty Backfill — {mode}**\n"
            f"Text channels: **{len(guild.text_channels):,}**\n"
            f"Channels completed: **{channels_completed:,}**\n"
            f"Channels failed/inaccessible: **{inaccessible:,}**\n"
            f"Messages scanned: **{scanned:,}**\n"
            f"Wager commands detected: **{total_wagers_detected:,}**\n"
            f"Ghosty messages found: **{total_ghosty_messages:,}**\n"
            f"Validated hits: **{counts['hit']:,}**\n"
            f"Validated misses: **{counts['miss']:,}**\n"
            f"Ignored/non-gambling Ghosty messages: **{counts['ignored']:,}**\n"
            f"Already recorded: **{counts['duplicates']:,}**\n"
            f"New historical candidates: **{counts['new_candidates']:,}**\n"
            f"Newly imported: **{counts['imported']:,}**\n\n"
            "Per-channel details are available in the bot logs.",
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(GhostyCog(bot))