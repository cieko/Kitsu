from __future__ import annotations

import discord

from .constants import CONTENT_TYPES


class WhitelistPage:
    def _build_page_three(self):

        self.clear_items()

        self.current_page = 3

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
                "Choose members and roles that should bypass "
                "the channel restrictions."
            )
        )

        # ----------------------------------------------------
        # Current whitelist
        # ----------------------------------------------------

        container.add_item(discord.ui.TextDisplay(self._whitelist_text()))

        # ----------------------------------------------------
        # Member selector
        # ----------------------------------------------------

        member_select = discord.ui.UserSelect(
            placeholder="Select members",
            min_values=0,
            max_values=25,
            custom_id="custom_whitelist_members",
        )

        async def member_callback(
            interaction: discord.Interaction,
        ):

            self.whitelist_users = {user.id for user in member_select.values}

            await interaction.response.edit_message(
                view=self,
            )

        member_select.callback = member_callback

        member_row = discord.ui.ActionRow()
        member_row.add_item(member_select)

        container.add_item(member_row)

        # ----------------------------------------------------
        # Role selector
        # ----------------------------------------------------

        role_select = discord.ui.RoleSelect(
            placeholder="Select roles",
            min_values=0,
            max_values=25,
            custom_id="custom_whitelist_roles",
        )

        async def role_callback(
            interaction: discord.Interaction,
        ):

            self.whitelist_roles = {role.id for role in role_select.values}

            await interaction.response.edit_message(
                view=self,
            )

        role_select.callback = role_callback

        role_row = discord.ui.ActionRow()
        role_row.add_item(role_select)

        container.add_item(role_row)

        self.add_item(container)

        # ----------------------------------------------------
        # Navigation
        # ----------------------------------------------------

        navigation = discord.ui.ActionRow()

        back_button = discord.ui.Button(
            label="◀",
            style=discord.ButtonStyle.secondary,
            custom_id="custom_page3_back",
        )

        skip_button = discord.ui.Button(
            label="Skip",
            style=discord.ButtonStyle.secondary,
            custom_id="custom_page3_skip",
        )

        save_button = discord.ui.Button(
            label="Save Configuration",
            style=discord.ButtonStyle.success,
            emoji="💾",
            custom_id="custom_final_save",
        )

        cancel_button = discord.ui.Button(
            label="Cancel",
            style=discord.ButtonStyle.danger,
            custom_id="custom_page3_cancel",
        )

        back_button.callback = self.page_three_back
        skip_button.callback = self.skip_page_three
        save_button.callback = self.save
        cancel_button.callback = self.cancel

        navigation.add_item(back_button)
        navigation.add_item(skip_button)
        navigation.add_item(save_button)
        navigation.add_item(cancel_button)

        self.add_item(navigation)

    def _whitelist_text(self) -> str:

        user_count = len(self.whitelist_users)
        role_count = len(self.whitelist_roles)

        return f"**Members:** `{user_count}`\n**Roles:** `{role_count}`"

    async def page_three_back(
        self,
        interaction: discord.Interaction,
    ):

        if self.rules["gif"]:
            self._build_page_two()
        else:
            self._build_page_one()

            for key in CONTENT_TYPES:
                self._update_button_state(key)

        await interaction.response.edit_message(
            view=self,
        )

    async def skip_page_three(
        self,
        interaction: discord.Interaction,
    ):

        self.whitelist_users.clear()
        self.whitelist_roles.clear()

        await self._save_configuration(
            interaction,
        )

    async def save(
        self,
        interaction: discord.Interaction,
    ):

        await self._save_configuration(
            interaction,
        )
