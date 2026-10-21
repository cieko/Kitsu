from __future__ import annotations

import logging
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

        if not self.refresh_dashboard.is_running():
            self.refresh_dashboard.start()

    def cog_unload(self) -> None:
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
        minimum_samples = int(await repository.get_config(
            guild.id, "minimum_samples", DEFAULT_MINIMUM_SAMPLES
        ))

        rows: list[dict[str, Any]] = []
        for stats in stats_documents:
            channel_id = int(stats["channel_id"])
            prediction = await self.service.get_prediction_data(
                guild.id, channel_id, minimum_samples=minimum_samples
            )
            rows.append({
                "channel_id": channel_id,
                "streak": int(stats.get("streak", 0)),
                "loss": int(stats.get("streak_loss", 0)),
                "hit_at": prediction.get("hit_at"),
                "hit_percent": prediction.get("hit_percent"),
                "ready": bool(prediction.get("ready")),
            })

        rows.sort(key=lambda row: (
            not row["ready"],
            row["hit_percent"] if row["ready"] and row["hit_percent"] is not None else float("inf"),
            row["channel_id"],
        ))

        channels_per_page = 5
        total_pages = max(1, (len(rows) + channels_per_page - 1) // channels_per_page)
        if page is None:
            page = int(await repository.get_config(guild.id, "dashboard_page", 0))
        page = max(0, min(page, total_pages - 1))
        await repository.set_config(guild.id, "dashboard_page", page)
        page_rows = rows[page * channels_per_page:(page + 1) * channels_per_page]

        def padded_label(value: str, width: int) -> str:
            value = str(value)
            if len(value) > width:
                value = value[:max(1, width - 1)] + "…"
            left = max(0, (width - len(value)) // 2)
            right = max(0, width - len(value) - left)
            return "\u2007" * left + value + "\u2007" * right

        view = discord.ui.LayoutView(timeout=None)
        container = discord.ui.Container(accent_colour=discord.Colour.dark_purple())
        container.add_item(discord.ui.TextDisplay(content="### Ghosty Market"))

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
                    label=padded_label(channel_name, 12),
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

        container.add_item(discord.ui.TextDisplay(
            content=f"-# zᶻ Refresh <t:{int(time.time()) + DASHBOARD_REFRESH_SECONDS}:R>"
        ))
        view.add_item(container)
        return view

    async def _repost_sticky_dashboard(self, guild: discord.Guild) -> None:
        """Move the dashboard to the bottom by reposting it after channel activity."""
        channel_id = await repository.get_config(guild.id, "dashboard_channel_id")
        old_message_id = await repository.get_config(guild.id, "dashboard_message_id")
        if not channel_id:
            return

        channel = guild.get_channel(int(channel_id))
        if not isinstance(channel, discord.TextChannel):
            return

        old_message = None
        if old_message_id:
            old_message = await self._get_message(channel, int(old_message_id))

        view = await self._build_dashboard_view(guild)
        try:
            new_message = await channel.send(view=view)
        except discord.HTTPException:
            logger.exception("Failed to repost Ghosty dashboard in guild %s.", guild.id)
            return

        await repository.set_config(guild.id, "dashboard_message_id", str(new_message.id))

        if old_message is not None:
            try:
                await old_message.delete()
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                logger.warning("Could not delete old Ghosty dashboard message %s.", old_message.id)

    async def _refresh_dashboard_for_guild(
        self,
        guild: discord.Guild,
    ) -> None:
        channel_id = await repository.get_config(
            guild.id,
            "dashboard_channel_id",
        )

        message_id = await repository.get_config(
            guild.id,
            "dashboard_message_id",
        )

        if not channel_id:
            return

        channel = guild.get_channel(int(channel_id))

        if not isinstance(channel, discord.TextChannel):
            return

        view = await self._build_dashboard_view(guild)

        if message_id:
            message = await self._get_message(
                channel,
                int(message_id),
            )

            if message is not None:
                # A legacy dashboard message contains normal message content and
                # cannot reliably be converted into a Components V2 message by
                # merely attaching a LayoutView. Delete it and create a fresh V2
                # message once, then future refreshes can edit that V2 message.
                is_components_v2 = bool(
                    getattr(message.flags, "is_components_v2", False)
                )
                if is_components_v2:
                    try:
                        await message.edit(view=view, content=None, embeds=[])
                        return
                    except discord.HTTPException:
                        logger.exception("Failed to edit Ghosty V2 dashboard.")
                else:
                    try:
                        await message.delete()
                    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                        logger.warning(
                            "Could not delete legacy Ghosty dashboard message %s.",
                            message.id,
                        )

        try:
            message = await channel.send(view=view)
        except discord.HTTPException:
            logger.exception(
                "Failed to create Ghosty dashboard."
            )
            return

        await repository.set_config(
            guild.id,
            "dashboard_message_id",
            str(message.id),
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
        source_message: discord.Message,
        hit_user_id: int,
    ) -> list[int]:
        """Collect users whose misses belong to the current channel streak."""
        participant_ids: set[int] = set()
        pending_result: discord.Message | None = None
        try:
            async for previous in source_message.channel.history(
                limit=1000, before=source_message, oldest_first=False
            ):
                if previous.author.id == GHOSTY_BOT_ID:
                    if pending_result is None:
                        pending_result = previous
                    continue

                if pending_result is None or detect_gamble(previous) is None:
                    continue

                matched = await self.service.analyze_messages(previous, pending_result)
                pending_result = None
                if matched is None:
                    continue
                if matched["outcome"] == "hit":
                    break
                participant_ids.add(previous.author.id)
        except (discord.Forbidden, discord.HTTPException):
            logger.exception(
                "Could not collect Ghosty streak participants for message %s.",
                source_message.id,
            )

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

                        source_message = await self._get_message(
                            message.channel, int(recorded["source_message_id"])
                        )
                        hit_user_id = int(recorded["user_id"])
                        participant_ids: list[int] = []
                        if source_message is not None:
                            participant_ids = await self._get_streak_participant_ids(
                                source_message, hit_user_id
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
                                f"Kudos to <@{hit_user_id}>\n"
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

                            await self._repost_sticky_dashboard(guild)
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
                await self._repost_sticky_dashboard(guild)
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

        await interaction.response.defer(
            ephemeral=True,
            thinking=True,
        )

        # Remove the previously configured dashboard before replacing it.
        # This also cleans up the old plain-text/legacy-embed dashboard when
        # /ghosty setup is run again, including when moving to another channel.
        old_channel_id = await repository.get_config(
            interaction.guild.id,
            "dashboard_channel_id",
        )
        old_message_id = await repository.get_config(
            interaction.guild.id,
            "dashboard_message_id",
        )
        if old_channel_id and old_message_id:
            old_channel = interaction.guild.get_channel(int(old_channel_id))
            if isinstance(old_channel, discord.TextChannel):
                old_message = await self._get_message(
                    old_channel,
                    int(old_message_id),
                )
                if old_message is not None:
                    try:
                        await old_message.delete()
                    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                        logger.warning(
                            "Could not delete previous Ghosty dashboard message %s.",
                            old_message.id,
                        )

        await repository.set_config(
            interaction.guild.id,
            "dashboard_channel_id",
            str(channel.id),
        )

        try:
            view = await self._build_dashboard_view(interaction.guild)
            message = await channel.send(view=view)
        except discord.HTTPException:
            await interaction.followup.send(
                "I couldn't post the dashboard there. Check that I have "
                "View Channel and Send Messages permissions.",
                ephemeral=True,
            )
            return

        await repository.set_config(
            interaction.guild.id,
            "dashboard_message_id",
            str(message.id),
        )

        await interaction.followup.send(
            f"Ghosty dashboard configured in {channel.mention}.",
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

        candidates = [source]
        matched_result = None
        ambiguous = False

        try:
            async for candidate in interaction.channel.history(
                limit=1000,
                after=source,
                oldest_first=True,
            ):
                if candidate.author.id == GHOSTY_BOT_ID:
                    matches = []

                    for gamble in candidates:
                        result = await self.service.analyze_messages(
                            gamble,
                            candidate,
                        )

                        if result is not None:
                            matches.append(
                                (gamble, result, candidate)
                            )

                    if len(matches) == 1:
                        matched_result = matches[0]
                    elif len(matches) > 1:
                        ambiguous = True

                    break

                if detect_gamble(candidate) is not None:
                    candidates.append(candidate)

        except (discord.Forbidden, discord.HTTPException):
            await interaction.followup.send(
                f"Detected wager: **{wager:,} Goos**.\n"
                "I couldn't scan the messages after this gamble. "
                "Check my View Channel and Read Message History permissions.",
                ephemeral=True,
            )
            return

        if ambiguous:
            await interaction.followup.send(
                f"Detected wager: **{wager:,} Goos**.\n"
                "The Ghosty result could match multiple gamble commands. "
                "No stats were changed.",
                ephemeral=True,
            )
            return

        if (
            matched_result is None
            or matched_result[0].id != source.id
        ):
            await interaction.followup.send(
                f"Detected wager: **{wager:,} Goos**.\n"
                "No confidently matched Ghosty result was found for this "
                "gamble before the next Ghosty message. No stats were changed.",
                ephemeral=True,
            )
            return

        _, result, response = matched_result

        await interaction.followup.send(
            f"**Wager:** {result['wager']:,} Goos\n"
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
        description="Scan accessible channel history for Ghosty gambles.",
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

        # Prevent historical imports from interleaving with live records.
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
                    "exist in the database. Importing older results now could "
                    "corrupt chronological streak statistics.",
                    ephemeral=True,
                )
                return

        if (
            import_results
            and confirmation != BACKFILL_IMPORT_CONFIRMATION
        ):
            await interaction.response.send_message(
                "To import results, set import_results to True and "
                "confirmation to IMPORT. Otherwise, run a dry run.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(
            ephemeral=True,
            thinking=True,
        )

        counts = defaultdict(int)

        inaccessible = 0
        scanned = 0
        channels_completed = 0
        channels_failed = 0
        total_wagers_detected = 0
        total_ghosty_messages = 0

        for channel in guild.text_channels:
            # Pending wager commands, maintained in chronological order.
            pending_gambles: list[discord.Message] = []

            channel_scanned = 0
            channel_wagers = 0
            channel_ghosty = 0
            channel_hits = 0
            channel_misses = 0
            channel_ignored = 0
            channel_unmatched = 0
            channel_duplicates = 0
            channel_imported = 0

            logger.info(
                "Ghosty backfill starting channel=%s (%s)",
                channel.name,
                channel.id,
            )

            try:
                async for message in channel.history(limit=None, oldest_first=True):
                    scanned += 1
                    channel_scanned += 1

                    # Collect supported wager commands.
                    wager = detect_gamble(message)

                    if wager is not None:
                        channel_wagers += 1
                        pending_gambles.append(message)

                        logger.debug(
                            "Ghosty backfill wager detected: "
                            "channel=%s channel_id=%s message_id=%s "
                            "author_id=%s wager=%s content=%r",
                            channel.name,
                            channel.id,
                            message.id,
                            message.author.id,
                            wager,
                            message.content[:200],
                        )
                        continue

                    # Ignore messages that are not from Ghosty.
                    if message.author.id != GHOSTY_BOT_ID:
                        continue

                    channel_ghosty += 1

                    if not pending_gambles:
                        counts["ghosty_without_wager"] += 1
                        logger.debug(
                            "Ghosty response without pending wager: "
                            "channel=%s channel_id=%s message_id=%s "
                            "content=%r",
                            channel.name,
                            channel.id,
                            message.id,
                            message.content[:200],
                        )
                        continue

                    # Try pending wagers from newest to oldest.
                    # If several wagers have the same amount, prefer the
                    # latest command rather than marking the response ambiguous.
                    matched_result = None
                    matched_source = None

                    for source_message in reversed(pending_gambles):
                        result = await self.service.analyze_messages(
                            source_message,
                            message,
                        )

                        if result is not None:
                            matched_result = result
                            matched_source = source_message
                            break

                    if matched_result is None or matched_source is None:
                        channel_ignored += 1
                        counts["ignored"] += 1

                        logger.warning(
                            "Ghosty backfill could not classify response: "
                            "channel=%s channel_id=%s response_id=%s "
                            "pending_wagers=%s content=%r",
                            channel.name,
                            channel.id,
                            message.id,
                            len(pending_gambles),
                            message.content[:200],
                        )

                        # Do not discard the entire pending list just because
                        # this response was not recognized.
                        continue

                    result = matched_result
                    outcome = result["outcome"]

                    counts[outcome] += 1

                    if outcome == "hit":
                        channel_hits += 1
                    else:
                        channel_misses += 1

                    logger.info(
                        "Ghosty backfill classified result: "
                        "channel=%s channel_id=%s outcome=%s "
                        "wager=%s payout=%s source_id=%s result_id=%s "
                        "user_id=%s",
                        channel.name,
                        channel.id,
                        outcome,
                        result.get("wager"),
                        result.get("payout"),
                        result.get("source_message_id"),
                        result.get("result_message_id"),
                        result.get("user_id"),
                    )

                    # Import only when explicitly requested.
                    if import_results:
                        recorded = await self.service.record_result(
                            result,
                            source="backfill",
                        )

                        if recorded is None:
                            counts["duplicates"] += 1
                            channel_duplicates += 1

                            logger.info(
                                "Ghosty backfill duplicate skipped: "
                                "channel=%s channel_id=%s source_id=%s",
                                channel.name,
                                channel.id,
                                result.get("source_message_id"),
                            )
                        else:
                            counts["imported"] += 1
                            channel_imported += 1

                    # Remove wagers up to and including the matched wager.
                    # Older unmatched commands must not be paired with a
                    # later Ghosty response accidentally.
                    matched_index = pending_gambles.index(matched_source)
                    removed = pending_gambles[:matched_index + 1]
                    del pending_gambles[:matched_index + 1]

                    # Commands discarded before the matched one did not
                    # receive a confidently matched result.
                    if len(removed) > 1:
                        unmatched_count = len(removed) - 1
                        channel_unmatched += unmatched_count
                        counts["unmatched"] += unmatched_count

                        logger.warning(
                            "Ghosty backfill discarded older unmatched "
                            "wagers: channel=%s channel_id=%s count=%s "
                            "message_ids=%s",
                            channel.name,
                            channel.id,
                            unmatched_count,
                            [item.id for item in removed[:-1]],
                        )

                # Count wager commands left without a matching response.
                if pending_gambles:
                    remaining = len(pending_gambles)
                    channel_unmatched += remaining
                    counts["unmatched"] += remaining

                    logger.warning(
                        "Ghosty backfill unmatched wagers at end of channel: "
                        "channel=%s channel_id=%s count=%s message_ids=%s",
                        channel.name,
                        channel.id,
                        remaining,
                        [item.id for item in pending_gambles],
                    )

                channels_completed += 1
                total_wagers_detected += channel_wagers
                total_ghosty_messages += channel_ghosty

                logger.info(
                    "Ghosty backfill channel summary: "
                    "channel=%s channel_id=%s scanned=%s "
                    "wagers_detected=%s ghosty_messages=%s "
                    "hits=%s misses=%s ignored=%s unmatched=%s "
                    "duplicates=%s imported=%s",
                    channel.name,
                    channel.id,
                    channel_scanned,
                    channel_wagers,
                    channel_ghosty,
                    channel_hits,
                    channel_misses,
                    channel_ignored,
                    channel_unmatched,
                    channel_duplicates,
                    channel_imported,
                )

            except discord.Forbidden:
                inaccessible += 1
                channels_failed += 1

                if pending_gambles:
                    remaining = len(pending_gambles)
                    channel_unmatched += remaining
                    counts["unmatched"] += remaining

                logger.warning(
                    "Ghosty backfill skipped channel=%s (%s) | "
                    "reason=Missing Access | messages_scanned=%s",
                    channel.name,
                    channel.id,
                    channel_scanned,
                )

                total_wagers_detected += channel_wagers
                total_ghosty_messages += channel_ghosty

            except discord.HTTPException as exc:
                inaccessible += 1
                channels_failed += 1

                if pending_gambles:
                    remaining = len(pending_gambles)
                    channel_unmatched += remaining
                    counts["unmatched"] += remaining

                logger.warning(
                    "Ghosty backfill skipped channel=%s (%s) | "
                    "reason=Discord HTTP error (%s) | messages_scanned=%s",
                    channel.name,
                    channel.id,
                    exc.status,
                    channel_scanned,
                )

                total_wagers_detected += channel_wagers
                total_ghosty_messages += channel_ghosty

        mode = "IMPORT" if import_results else "DRY RUN"

        logger.info(
            "Ghosty backfill final summary: "
            "mode=%s guild=%s guild_id=%s "
            "channels_total=%s channels_completed=%s channels_failed=%s "
            "messages_scanned=%s wagers_detected=%s ghosty_messages=%s "
            "hits=%s misses=%s ignored=%s unmatched=%s "
            "ghosty_without_wager=%s duplicates=%s imported=%s "
            "inaccessible=%s",
            mode,
            guild.name,
            guild.id,
            len(guild.text_channels),
            channels_completed,
            channels_failed,
            scanned,
            total_wagers_detected,
            total_ghosty_messages,
            counts["hit"],
            counts["miss"],
            counts["ignored"],
            counts["unmatched"],
            counts["ghosty_without_wager"],
            counts["duplicates"],
            counts["imported"],
            inaccessible,
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
            f"Ignored/ambiguous results: **{counts['ignored']:,}**\n"
            f"Unmatched wager commands: **{counts['unmatched']:,}**\n"
            f"Ghosty messages without pending wagers: "
            f"**{counts['ghosty_without_wager']:,}**\n"
            f"Already recorded: **{counts['duplicates']:,}**\n"
            f"Newly imported: **{counts['imported']:,}**\n\n"
            "Per-channel details are available in the bot's logs.",
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(GhostyCog(bot))