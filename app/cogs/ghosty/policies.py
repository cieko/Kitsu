from __future__ import annotations

import discord


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

    container.add_item(discord.ui.TextDisplay(content="## Ghosty Dashboard — Tracking Policy"))

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
                "It is the estimated average hit position if further "
                "attempts share the current session conditions. "
                "It is not a guaranteed winning attempt.\n\n"
                "**What does the percentage mean?**\n"
                "It is the estimate produced by that prediction logic, "
                "not a guarantee or necessarily the true probability "
                "of the next gamble.\n\n"
                "**Which patterns are considered?**\n"
                "Daily sessions begin at 05:30 AM IST. The model tests "
                "each channel's age, creation in the current reset day, "
                "time since its last gamble, whether that gamble was "
                "before the reset, channels under 24 hours old, and "
                "attempts 5–15 after a completed run of at least 20 misses. "
                "These are hypotheses: adjustments need enough observations "
                "and better performance on later recorded results. "
                "Otherwise historical rates are used. Daily sessions "
                "do not erase tracked statistics or history.\n\n"
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
