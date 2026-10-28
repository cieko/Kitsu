from __future__ import annotations

import discord

from .policies import show_policy, show_terms
from .settings_menu import show_settings_menu


class DashboardControls:
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

        terms_button.callback = show_terms
        policy_button.callback = show_policy

        # The gear MUST open the menu, not the channel-list settings directly.
        settings_button.callback = lambda interaction: show_settings_menu(self, interaction)

        row.add_item(terms_button)
        row.add_item(policy_button)
        row.add_item(settings_button)

        controls.add_item(row)
        view.add_item(controls)

        return view
