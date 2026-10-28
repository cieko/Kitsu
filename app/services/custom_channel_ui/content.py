from __future__ import annotations

import discord

from .constants import CONTENT_TYPES, DEFAULT_RULES


class ContentPage:
    def _build_page_one(self):

        self.clear_items()

        self.current_page = 1

        self.label_buttons.clear()
        self.tick_buttons.clear()
        self.cross_buttons.clear()

        # ----------------------------------------------------
        # Container
        # ----------------------------------------------------

        container = discord.ui.Container(
            accent_color=discord.Color.from_rgb(115, 5, 43),
        )

        # ----------------------------------------------------
        # Header
        # ----------------------------------------------------

        container.add_item(
            discord.ui.TextDisplay(
                f"# [⚙️](https://discord.com/assets/7afdc0163bb3fba3.svg) "
                f"Custom Channel Setup\n"
                f"**Channel:** {self.channel.mention}\n\n"
                "**1. Content Types**\n"
                "Choose which types of content are allowed."
            )
        )

        # ----------------------------------------------------
        # Content type rows
        #
        # Each row:
        #
        # Text : false          [ ✓ ] [ ✕ ]
        #
        # The disabled button acts as the label/status.
        # ----------------------------------------------------

        for key, label in CONTENT_TYPES.items():
            row = discord.ui.ActionRow()

            # ------------------------------------------------
            # Label / status button
            # ------------------------------------------------

            label_button = discord.ui.Button(
                label=self._status_label(
                    label,
                    self.rules[key],
                ),
                style=discord.ButtonStyle.secondary,
                disabled=True,
            )

            # ------------------------------------------------
            # Tick button
            # ------------------------------------------------

            tick_button = discord.ui.Button(
                style=discord.ButtonStyle.secondary,
                emoji=self.emojis.get(
                    "tickchecked" if self.rules[key] else "tickunchecked",
                    "☑️" if self.rules[key] else "⬜",
                ),
                custom_id=f"custom_tick_{key}",
            )

            # ------------------------------------------------
            # Cross button
            # ------------------------------------------------

            cross_button = discord.ui.Button(
                style=discord.ButtonStyle.secondary,
                emoji=self.emojis.get(
                    "crossunchecked" if self.rules[key] else "crosschecked",
                    "⬜" if self.rules[key] else "❌",
                ),
                custom_id=f"custom_cross_{key}",
            )

            # ------------------------------------------------
            # Callbacks
            # ------------------------------------------------

            tick_button.callback = self._make_toggle_callback(
                key,
                True,
            )

            cross_button.callback = self._make_toggle_callback(
                key,
                False,
            )

            # ------------------------------------------------
            # Store references
            # ------------------------------------------------

            self.label_buttons[key] = label_button
            self.tick_buttons[key] = tick_button
            self.cross_buttons[key] = cross_button

            # ------------------------------------------------
            # Add to row
            # ------------------------------------------------

            row.add_item(label_button)
            row.add_item(tick_button)
            row.add_item(cross_button)

            container.add_item(row)

        # ----------------------------------------------------
        # Tip
        # ----------------------------------------------------

        container.add_item(
            discord.ui.TextDisplay(
                "**Tip:** Configure GIF domains and whitelist members/roles on the next pages."
            )
        )

        self.add_item(container)

        # ----------------------------------------------------
        # Navigation
        # ----------------------------------------------------

        navigation = discord.ui.ActionRow()

        skip_button = discord.ui.Button(
            label="Skip",
            style=discord.ButtonStyle.secondary,
            custom_id="custom_page1_skip",
        )

        continue_button = discord.ui.Button(
            label="Save & Continue",
            style=discord.ButtonStyle.primary,
            custom_id="custom_page1_continue",
        )

        cancel_button = discord.ui.Button(
            label="Cancel",
            style=discord.ButtonStyle.danger,
            custom_id="custom_page1_cancel",
        )

        skip_button.callback = self.skip_page_one
        continue_button.callback = self.page_one_continue
        cancel_button.callback = self.cancel

        navigation.add_item(skip_button)
        navigation.add_item(continue_button)
        navigation.add_item(cancel_button)

        self.add_item(navigation)

    @staticmethod
    def _status_label(
        label: str,
        enabled: bool,
    ) -> str:

        return f"{label} : {str(enabled).lower()}"

    def _update_button_state(
        self,
        key: str,
    ):

        enabled = self.rules[key]
        label = CONTENT_TYPES[key]

        # ----------------------------------------------------
        # Update label/status
        # ----------------------------------------------------

        if key in self.label_buttons:
            self.label_buttons[key].label = self._status_label(
                label,
                enabled,
            )

        # ----------------------------------------------------
        # Update tick button
        # ----------------------------------------------------

        if key in self.tick_buttons:
            self.tick_buttons[key].emoji = self.emojis.get(
                "tickchecked" if enabled else "tickunchecked",
                "☑️" if enabled else "⬜",
            )

        # ----------------------------------------------------
        # Update cross button
        # ----------------------------------------------------

        if key in self.cross_buttons:
            self.cross_buttons[key].emoji = self.emojis.get(
                "crossunchecked" if enabled else "crosschecked",
                "⬜" if enabled else "❌",
            )

    def _make_toggle_callback(
        self,
        key: str,
        value: bool,
    ):

        async def callback(
            interaction: discord.Interaction,
        ):

            self.rules[key] = value

            self._update_button_state(key)

            await interaction.response.edit_message(
                view=self,
            )

        return callback

    async def page_one_continue(
        self,
        interaction: discord.Interaction,
    ):

        if self.rules["gif"]:
            self._build_page_two()
        else:
            self._build_page_three()

        await interaction.response.edit_message(
            view=self,
        )

    async def skip_page_one(
        self,
        interaction: discord.Interaction,
    ):

        self.rules = DEFAULT_RULES.copy()

        self._build_page_three()

        await interaction.response.edit_message(
            view=self,
        )
