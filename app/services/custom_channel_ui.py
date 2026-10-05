import discord

from app.database.repositories.channel_repository import (
    set_channel_config,
)
from app.services.emoji_services import (
    get_application_emoji,
)


# ============================================================
# CONTENT TYPES
# ============================================================

CONTENT_TYPES = {
    "text": "Text",
    "custom_emoji": "Emoji",
    "gif": "GIF",
    "images": "Images",
    "videos": "Videos",
    "links": "Links",
    "stickers": "Stickers",
}


DEFAULT_RULES = {
    "text": False,
    "custom_emoji": False,
    "gif": False,
    "images": False,
    "videos": False,
    "links": False,
    "stickers": False,
}


DEFAULT_GIF_DOMAINS = [
    "tenor.com",
    "media.tenor.com",
    "giphy.com",
    "media.giphy.com",
    "i.giphy.com",
]


# ============================================================
# CUSTOM CHANNEL VIEW
# ============================================================


class CustomChannelView(discord.ui.LayoutView):

    def __init__(
        self,
        guild_id: int,
        channel: discord.TextChannel,
        bot: discord.Client,
    ):
        super().__init__(timeout=300)

        self.guild_id = guild_id
        self.channel = channel
        self.bot = bot

        # ----------------------------------------------------
        # Temporary configuration
        #
        # Nothing is saved to MongoDB until final save.
        # ----------------------------------------------------

        self.rules = DEFAULT_RULES.copy()

        self.gif_domains = DEFAULT_GIF_DOMAINS.copy()

        self.whitelist_users: set[int] = set()
        self.whitelist_roles: set[int] = set()

        # ----------------------------------------------------
        # Application emoji cache
        # ----------------------------------------------------

        self.emojis: dict[str, str] = {}

        # ----------------------------------------------------
        # Page state
        # ----------------------------------------------------

        self.current_page = 1

        # ----------------------------------------------------
        # Page 1 button references
        # ----------------------------------------------------

        self.label_buttons: dict[str, discord.ui.Button] = {}
        self.tick_buttons: dict[str, discord.ui.Button] = {}
        self.cross_buttons: dict[str, discord.ui.Button] = {}

        # ----------------------------------------------------
        # Build loading state first.
        #
        # The actual Page 1 UI is built after the application
        # emojis have finished loading.
        # ----------------------------------------------------

        self._build_loading_state()

    # ========================================================
    # LOADING STATE
    # ========================================================

    def _build_loading_state(self):

        self.clear_items()

        self.current_page = 1

        container = discord.ui.Container(
            accent_color=discord.Color.from_rgb(115, 5, 43),
        )

        container.add_item(
            discord.ui.TextDisplay(
                f"# [⚙️](https://discord.com/assets/7afdc0163bb3fba3.svg) "
                f"Custom Channel Setup\n"
                f"**Channel:** {self.channel.mention}\n\n"
                "⏳ **Loading...**\n"
                "Preparing the channel controls."
            )
        )

        self.add_item(container)

    # ========================================================
    # PAGE 1
    # ========================================================

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
                    "tickchecked"
                    if self.rules[key]
                    else "tickunchecked",
                    "☑️"
                    if self.rules[key]
                    else "⬜",
                ),
                custom_id=f"custom_tick_{key}",
            )

            # ------------------------------------------------
            # Cross button
            # ------------------------------------------------

            cross_button = discord.ui.Button(
                style=discord.ButtonStyle.secondary,
                emoji=self.emojis.get(
                    "crossunchecked"
                    if self.rules[key]
                    else "crosschecked",
                    "⬜"
                    if self.rules[key]
                    else "❌",
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
                "**Tip:** Configure GIF domains and "
                "whitelist members/roles on the next pages."
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

    # ========================================================
    # STATUS LABEL
    # ========================================================

    @staticmethod
    def _status_label(
        label: str,
        enabled: bool,
    ) -> str:

        return f"{label} : {str(enabled).lower()}"

    # ========================================================
    # INITIALIZE APPLICATION EMOJIS
    # ========================================================

    async def initialize(self):

        self.emojis["tickchecked"] = await get_application_emoji(
            self.bot,
            "tickchecked",
            "☑️",
        )

        self.emojis["tickunchecked"] = await get_application_emoji(
            self.bot,
            "tickunchecked",
            "⬜",
        )

        self.emojis["crosschecked"] = await get_application_emoji(
            self.bot,
            "crosschecked",
            "❌",
        )

        self.emojis["crossunchecked"] = await get_application_emoji(
            self.bot,
            "crossunchecked",
            "⬜",
        )

        # ----------------------------------------------------
        # Now that the emojis are loaded, build the actual UI.
        # ----------------------------------------------------

        self._build_page_one()

    # ========================================================
    # UPDATE BUTTON STATE
    # ========================================================

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

            self.label_buttons[key].label = (
                self._status_label(
                    label,
                    enabled,
                )
            )

        # ----------------------------------------------------
        # Update tick button
        # ----------------------------------------------------

        if key in self.tick_buttons:

            self.tick_buttons[key].emoji = (
                self.emojis.get(
                    "tickchecked"
                    if enabled
                    else "tickunchecked",
                    "☑️"
                    if enabled
                    else "⬜",
                )
            )

        # ----------------------------------------------------
        # Update cross button
        # ----------------------------------------------------

        if key in self.cross_buttons:

            self.cross_buttons[key].emoji = (
                self.emojis.get(
                    "crossunchecked"
                    if enabled
                    else "crosschecked",
                    "⬜"
                    if enabled
                    else "❌",
                )
            )

    # ========================================================
    # TOGGLE CALLBACK
    # ========================================================

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

    # ========================================================
    # PAGE 1 -> PAGE 2
    # ========================================================

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

    # ========================================================
    # PAGE 1 SKIP
    # ========================================================

    async def skip_page_one(
        self,
        interaction: discord.Interaction,
    ):

        self.rules = DEFAULT_RULES.copy()

        self._build_page_three()

        await interaction.response.edit_message(
            view=self,
        )

    # ========================================================
    # PAGE 2
    # ========================================================

    def _build_page_two(self):

        self.clear_items()

        self.current_page = 2

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
                "Choose which domains are allowed for GIF links."
            )
        )

        # ----------------------------------------------------
        # Current domains
        # ----------------------------------------------------

        container.add_item(
            discord.ui.TextDisplay(
                self._gif_domains_text()
            )
        )

        # ----------------------------------------------------
        # Edit domains
        # ----------------------------------------------------

        edit_row = discord.ui.ActionRow()

        edit_button = discord.ui.Button(
            label="Edit Domains",
            style=discord.ButtonStyle.secondary,
            emoji="✏️",
            custom_id="custom_edit_domains",
        )

        edit_button.callback = self.edit_gif_domains

        edit_row.add_item(edit_button)

        container.add_item(edit_row)

        self.add_item(container)

        # ----------------------------------------------------
        # Navigation
        # ----------------------------------------------------

        navigation = discord.ui.ActionRow()

        back_button = discord.ui.Button(
            label="◀",
            style=discord.ButtonStyle.secondary,
            custom_id="custom_page2_back",
        )

        skip_button = discord.ui.Button(
            label="Skip",
            style=discord.ButtonStyle.secondary,
            custom_id="custom_page2_skip",
        )

        continue_button = discord.ui.Button(
            label="Save & Continue",
            style=discord.ButtonStyle.primary,
            custom_id="custom_page2_continue",
        )

        cancel_button = discord.ui.Button(
            label="Cancel",
            style=discord.ButtonStyle.danger,
            custom_id="custom_page2_cancel",
        )

        back_button.callback = self.page_two_back
        skip_button.callback = self.skip_page_two
        continue_button.callback = self.page_two_continue
        cancel_button.callback = self.cancel

        navigation.add_item(back_button)
        navigation.add_item(skip_button)
        navigation.add_item(continue_button)
        navigation.add_item(cancel_button)

        self.add_item(navigation)

    # ========================================================
    # GIF DOMAIN TEXT
    # ========================================================

    def _gif_domains_text(self) -> str:

        if "*" in self.gif_domains:
            return (
                "**Allowed Domains**\n"
                "🌐 All GIF domains are allowed."
            )

        if not self.gif_domains:

            return (
                "**Allowed Domains**\n"
                "`None`"
            )

        domains = "\n".join(
            f"• `{domain}`"
            for domain in self.gif_domains
        )

        return (
            "**Allowed Domains**\n"
            f"{domains}"
        )

    # ========================================================
    # EDIT GIF DOMAINS
    # ========================================================

    async def edit_gif_domains(
        self,
        interaction: discord.Interaction,
    ):

        modal = GIFDomainsModal(
            self,
        )

        await interaction.response.send_modal(
            modal,
        )

    # ========================================================
    # PAGE 2 BACK
    # ========================================================

    async def page_two_back(
        self,
        interaction: discord.Interaction,
    ):

        self._build_page_one()

        for key in CONTENT_TYPES:
            self._update_button_state(key)

        await interaction.response.edit_message(
            view=self,
        )

    # ========================================================
    # PAGE 2 SKIP
    # ========================================================

    async def skip_page_two(
        self,
        interaction: discord.Interaction,
    ):

        self.gif_domains = DEFAULT_GIF_DOMAINS.copy()

        self._build_page_three()

        await interaction.response.edit_message(
            view=self,
        )

    # ========================================================
    # PAGE 2 -> PAGE 3
    # ========================================================

    async def page_two_continue(
        self,
        interaction: discord.Interaction,
    ):

        self._build_page_three()

        await interaction.response.edit_message(
            view=self,
        )

    # ========================================================
    # PAGE 3
    # ========================================================

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

        container.add_item(
            discord.ui.TextDisplay(
                self._whitelist_text()
            )
        )

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

            self.whitelist_users = {
                user.id
                for user in member_select.values
            }

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

            self.whitelist_roles = {
                role.id
                for role in role_select.values
            }

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

    # ========================================================
    # WHITELIST TEXT
    # ========================================================

    def _whitelist_text(self) -> str:

        user_count = len(self.whitelist_users)
        role_count = len(self.whitelist_roles)

        return (
            f"**Members:** `{user_count}`\n"
            f"**Roles:** `{role_count}`"
        )

    # ========================================================
    # PAGE 3 BACK
    # ========================================================

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

    # ========================================================
    # PAGE 3 SKIP
    # ========================================================

    async def skip_page_three(
        self,
        interaction: discord.Interaction,
    ):

        self.whitelist_users.clear()
        self.whitelist_roles.clear()

        await self._save_configuration(
            interaction,
        )

    # ========================================================
    # FINAL SAVE
    # ========================================================

    async def save(
        self,
        interaction: discord.Interaction,
    ):

        await self._save_configuration(
            interaction,
        )

    async def _save_configuration(
        self,
        interaction: discord.Interaction,
    ):

        # ----------------------------------------------------
        # ONLY HERE do we write to MongoDB.
        # ----------------------------------------------------

        await set_channel_config(
            guild_id=self.guild_id,
            channel_id=self.channel.id,
            preset="custom",
            rules=self.rules,
            gif_domains=self.gif_domains,
            whitelist_users=list(
                self.whitelist_users
            ),
            whitelist_roles=list(
                self.whitelist_roles
            ),
        )

        result_view = discord.ui.LayoutView()

        result_view.add_item(
            discord.ui.TextDisplay(
                f"# ✅ Configuration Saved\n\n"
                f"Custom configuration saved for "
                f"{self.channel.mention}."
            )
        )

        await interaction.response.edit_message(
            view=result_view,
        )

        self.stop()

    # ========================================================
    # CANCEL / ROLLBACK
    # ========================================================

    async def cancel(
        self,
        interaction: discord.Interaction,
    ):

        # ----------------------------------------------------
        # Nothing was written to MongoDB.
        # Simply discard temporary state.
        # ----------------------------------------------------

        self.rules = DEFAULT_RULES.copy()
        self.gif_domains = DEFAULT_GIF_DOMAINS.copy()

        self.whitelist_users.clear()
        self.whitelist_roles.clear()

        result_view = discord.ui.LayoutView()

        result_view.add_item(
            discord.ui.TextDisplay(
                "# ↩️ Setup Rolled Back\n\n"
                "All changes made during setup were discarded."
            )
        )

        await interaction.response.edit_message(
            view=result_view,
        )

        self.stop()


# ============================================================
# GIF DOMAINS MODAL
# ============================================================

class GIFDomainsModal(discord.ui.Modal):

    def __init__(
        self,
        view: CustomChannelView,
    ):
        super().__init__(
            title="GIF Domains",
        )

        self.parent_view = view

        self.domains_input = discord.ui.TextInput(
            label="Allowed GIF domains",
            placeholder="tenor.com, giphy.com or * for all",
            required=False,
            max_length=1000,
        )

        self.add_item(
            self.domains_input
        )

    async def on_submit(
        self,
        interaction: discord.Interaction,
    ):

        raw_domains = self.domains_input.value.strip()

        # ----------------------------------------------------
        # Empty input
        # ----------------------------------------------------

        if not raw_domains:
            self.parent_view.gif_domains = []

        # ----------------------------------------------------
        # Wildcard
        #
        # "*" means all GIF domains.
        # ----------------------------------------------------

        elif "*" in raw_domains:

            self.parent_view.gif_domains = ["*"]

        # ----------------------------------------------------
        # Normal domains
        # ----------------------------------------------------

        else:

            domains = []

            for domain in raw_domains.split(","):

                domain = domain.strip().lower()

                if not domain:
                    continue

                domain = domain.removeprefix(
                    "https://"
                )

                domain = domain.removeprefix(
                    "http://"
                )

                domain = domain.rstrip("/")

                if domain not in domains:
                    domains.append(domain)

            self.parent_view.gif_domains = domains

        await interaction.response.edit_message(
            view=self.parent_view
        )

    def __init__(
        self,
        view: CustomChannelView,
    ):
        super().__init__(
            title="GIF Domains",
        )

        self.parent_view = view

        self.domains_input = discord.ui.TextInput(
            label="Allowed GIF domains",
            placeholder="tenor.com, giphy.com",
            # default=", ".join(
            #     view.gif_domains
            # ),
            required=False,
            max_length=1000,
        )

        self.add_item(
            self.domains_input
        )

    async def on_submit(
        self,
        interaction: discord.Interaction,
    ):

        raw_domains = self.domains_input.value.strip()

        # ----------------------------------------------------
        # Empty input
        # ----------------------------------------------------

        if not raw_domains:

            self.parent_view.gif_domains = []

        # ----------------------------------------------------
        # Wildcard
        # ----------------------------------------------------

        elif "*" in raw_domains:

            self.parent_view.gif_domains = ["*"]

        # ----------------------------------------------------
        # Normal domains
        # ----------------------------------------------------

        else:

            domains = []

            for domain in raw_domains.split(","):

                domain = domain.strip().lower()

                if not domain:
                    continue

                domain = domain.removeprefix(
                    "https://"
                )

                domain = domain.removeprefix(
                    "http://"
                )

                domain = domain.rstrip("/")

                if domain not in domains:
                    domains.append(domain)

            self.parent_view.gif_domains = domains

        # ----------------------------------------------------
        # Rebuild Page 2 so the new list is displayed
        # immediately.
        # ----------------------------------------------------

        self.parent_view._build_page_two()

        await interaction.response.edit_message(
            view=self.parent_view
        )