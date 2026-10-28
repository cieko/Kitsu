from __future__ import annotations

from collections import defaultdict

import discord
from discord import app_commands

from app.database.repositories import ghosty_repository as repository
from app.services.ghosty_service import GHOSTY_BOT_ID, detect_gamble

from .constants import BACKFILL_IMPORT_CONFIRMATION, logger
from .groups import ghosty


class BackfillCommands:
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
