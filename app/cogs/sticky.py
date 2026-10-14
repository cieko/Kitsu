
import asyncio

import discord
from discord import app_commands
from discord.ext import commands

from app.database.repositories.sticky_repository import (
    delete_sticky,
    get_stickies,
    get_sticky,
    set_sticky,
    set_sticky_active,
    set_sticky_ignore_bots,
    update_sticky_message_id,
)
from app.services.sticky_service import StickyService


# --------------------------------------------------
# HELPERS
# --------------------------------------------------

def get_channel_name(guild: discord.Guild, channel_id: int) -> str:
    channel = guild.get_channel(channel_id)

    if isinstance(channel, discord.TextChannel):
        return f"#{channel.name}"

    return str(channel_id)


def get_sticky_preview(sticky: dict, limit: int = 180) -> str:
    script = " ".join(sticky.get("message_script", "").split())

    if not script:
        return "*Empty message*"

    if len(script) > limit:
        script = script[: limit - 3] + "..."

    return script


def build_sticky_options(
    guild: discord.Guild,
    stickies: list[dict],
) -> list[discord.SelectOption]:
    return [
        discord.SelectOption(
            label=get_channel_name(guild, sticky["channel_id"])[:100],
            value=str(sticky["channel_id"]),
            description=(
                "Ignore bots: "
                + ("ON" if sticky.get("ignore_bots", True) else "OFF")
            ),
        )
        for sticky in stickies[:25]
    ]


# --------------------------------------------------
# DYNAMIC COMPONENT BUTTON
# --------------------------------------------------

class StickyActionButton(discord.ui.Button):
    def __init__(
        self,
        *,
        label: str,
        style: discord.ButtonStyle,
        callback,
        emoji: str | None = None,
        disabled: bool = False,
    ):
        super().__init__(
            label=label,
            style=style,
            emoji=emoji,
            disabled=disabled,
        )
        self.action_callback = callback

    async def callback(self, interaction: discord.Interaction):
        await self.action_callback(interaction)


# --------------------------------------------------
# STICKY EDIT MODAL
# --------------------------------------------------

class StickyEditModal(discord.ui.Modal):
    def __init__(self, manager: "StickyListView", script: str):
        super().__init__(title="Edit Sticky Message")

        self.manager = manager

        self.message_input = discord.ui.TextInput(
            label="Message or embed script",
            style=discord.TextStyle.paragraph,
            default=script[:4000],
            max_length=4000,
            required=True,
        )
        self.add_item(self.message_input)

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        channel_id = self.manager.selected_channel_id

        if guild is None or channel_id is None:
            await interaction.response.send_message(
                "Select a sticky message first.",
                ephemeral=True,
            )
            return

        try:
            script = self.message_input.value
            content, embed = self.manager.sticky_service.render(
                script,
                guild,
            )

            if not content and embed is None:
                raise ValueError("The sticky message cannot be empty.")

            sticky = await get_sticky(guild.id, channel_id)
            if sticky is None:
                await interaction.response.send_message(
                    "That sticky no longer exists.",
                    ephemeral=True,
                )
                return

            await set_sticky(
                guild_id=guild.id,
                channel_id=channel_id,
                message_script=script,
                created_by=interaction.user.id,
            )

            self.manager.stickies = await get_stickies(guild.id)

            # Update the existing Components V2 message.
            await self.manager.show_detail(interaction)

        except ValueError as exc:
            await interaction.response.send_message(
                f"Invalid sticky script: {exc}",
                ephemeral=True,
            )


# --------------------------------------------------
# STICKY SELECT MENU
# --------------------------------------------------

class StickyChannelSelect(discord.ui.Select):
    def __init__(self, manager: "StickyListView"):
        self.manager = manager

        options = build_sticky_options(
            manager.guild,
            manager.stickies,
        )

        super().__init__(
            placeholder="Select a sticky message...",
            min_values=1,
            max_values=1,
            options=options,
            disabled=not options,
        )

    async def callback(self, interaction: discord.Interaction):
        self.manager.selected_channel_id = int(self.values[0])
        await self.manager.show_detail(interaction)


# --------------------------------------------------
# STICKY MANAGER — COMPONENTS V2
# --------------------------------------------------

class StickyListView(discord.ui.LayoutView):
    def __init__(
        self,
        guild: discord.Guild,
        stickies: list[dict],
    ):
        super().__init__(timeout=300)

        self.guild = guild
        self.stickies = stickies
        self.selected_channel_id: int | None = None
        self.sticky_service = StickyService()

        self.show_home()

    async def interaction_check(
        self,
        interaction: discord.Interaction,
    ) -> bool:
        if interaction.guild is None:
            await interaction.response.send_message(
                "This control can only be used inside a server.",
                ephemeral=True,
            )
            return False

        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "Unable to verify your server permissions.",
                ephemeral=True,
            )
            return False

        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "You need Administrator permission to manage stickies.",
                ephemeral=True,
            )
            return False

        return True

    def selected_sticky(self) -> dict | None:
        return next(
            (
                sticky
                for sticky in self.stickies
                if sticky["channel_id"] == self.selected_channel_id
            ),
            None,
        )

    # --------------------------------------------------
    # HOME / LIST SCREEN
    # --------------------------------------------------

    def show_home(self):
        self.clear_items()

        container = discord.ui.Container(
            discord.ui.TextDisplay(
                f"## Sticky messages\n"
                f"-# Entries · {len(self.stickies)} configured"
            ),
            discord.ui.Separator(),
        )

        if not self.stickies:
            container.add_item(
                discord.ui.TextDisplay(
                    "No sticky messages configured."
                )
            )
        else:
            for sticky in self.stickies[:25]:
                channel_name = get_channel_name(
                    self.guild,
                    sticky["channel_id"],
                )

                preview = get_sticky_preview(sticky)

                container.add_item(
                    discord.ui.TextDisplay(
                        f"> **{channel_name}**\n"
                        f"> {preview}"
                    )
                )
                container.add_item(discord.ui.Separator())

            container.add_item(
                discord.ui.ActionRow(
                    StickyChannelSelect(self)
                )
            )

            if len(self.stickies) > 25:
                container.add_item(
                    discord.ui.TextDisplay(
                        "-# Only the first 25 entries are selectable."
                    )
                )

        self.add_item(container)

    # --------------------------------------------------
    # DETAIL SCREEN
    # --------------------------------------------------

    async def show_detail(
        self,
        interaction: discord.Interaction,
    ):
        guild = interaction.guild

        if guild is None:
            return

        self.stickies = await get_stickies(guild.id)
        sticky = self.selected_sticky()

        if sticky is None:
            self.selected_channel_id = None
            self.show_home()

            await interaction.response.edit_message(view=self)
            return

        self.clear_items()

        channel_id = sticky["channel_id"]
        channel_name = get_channel_name(guild, channel_id)

        script = sticky.get("message_script", "").strip()
        if not script:
            script = "*Empty message*"

        # Avoid exceeding Discord's TextDisplay component limit.
        if len(script) > 3900:
            script = script[:3897] + "..."

        ignore_bots = sticky.get("ignore_bots", True)

        edit_button = StickyActionButton(
            label="Edit",
            style=discord.ButtonStyle.secondary,
            callback=self.edit_selected,
        )

        ignore_button = StickyActionButton(
            label="On" if ignore_bots else "Off",
            style=(
                discord.ButtonStyle.success
                if ignore_bots
                else discord.ButtonStyle.secondary
            ),
            callback=self.toggle_ignore_bots,
        )

        container = discord.ui.Container(
            discord.ui.TextDisplay(
                f"### {channel_name}\n"
                "-# Sticky messages"
            ),
            discord.ui.Separator(),
            discord.ui.Section(
                discord.ui.TextDisplay(
                    f"**Message**\n"
                    f"{script}"
                ),
                accessory=edit_button,
            ),
            discord.ui.Separator(),
            discord.ui.Section(
                discord.ui.TextDisplay(
                    "**Ignore bots**\n"
                    "Do not repost after a bot's message"
                ),
                accessory=ignore_button,
            ),
            discord.ui.Separator(),
            accent_colour=discord.Colour.from_rgb(180, 214, 213),
        )

        back_button = StickyActionButton(
            label="Back",
            style=discord.ButtonStyle.secondary,
            callback=self.back_to_list,
        )

        home_button = StickyActionButton(
            label="Home",
            style=discord.ButtonStyle.secondary,
            callback=self.back_to_list,
        )

        remove_button = StickyActionButton(
            label="Remove",
            style=discord.ButtonStyle.danger,
            callback=self.remove_selected,
        )

        container.add_item(
            discord.ui.ActionRow(
                back_button,
                remove_button,
            )
        )

        self.add_item(container)

        await interaction.response.edit_message(view=self)

    # --------------------------------------------------
    # EDIT
    # --------------------------------------------------

    async def edit_selected(self, interaction: discord.Interaction):
        sticky = self.selected_sticky()

        if sticky is None:
            await interaction.response.send_message(
                "Select a sticky message first.",
                ephemeral=True,
            )
            return

        await interaction.response.send_modal(
            StickyEditModal(
                self,
                sticky.get("message_script", ""),
            )
        )

    # --------------------------------------------------
    # TOGGLE IGNORE BOTS
    # --------------------------------------------------

    async def toggle_ignore_bots(
        self,
        interaction: discord.Interaction,
    ):
        guild = interaction.guild

        if guild is None or self.selected_channel_id is None:
            await interaction.response.send_message(
                "Select a sticky message first.",
                ephemeral=True,
            )
            return

        sticky = await get_sticky(
            guild.id,
            self.selected_channel_id,
        )

        if sticky is None:
            await interaction.response.send_message(
                "That sticky no longer exists.",
                ephemeral=True,
            )
            return

        current = sticky.get("ignore_bots", True)

        updated = await set_sticky_ignore_bots(
            guild_id=guild.id,
            channel_id=self.selected_channel_id,
            ignore_bots=not current,
        )

        if not updated:
            await interaction.response.send_message(
                "Couldn't update this sticky.",
                ephemeral=True,
            )
            return

        await self.show_detail(interaction)

    # --------------------------------------------------
    # BACK / HOME
    # --------------------------------------------------

    async def back_to_list(self, interaction: discord.Interaction):
        self.selected_channel_id = None
        self.stickies = await get_stickies(self.guild.id)

        self.show_home()

        await interaction.response.edit_message(view=self)

    # --------------------------------------------------
    # REMOVE
    # --------------------------------------------------

    async def remove_selected(self, interaction: discord.Interaction):
        guild = interaction.guild
        channel_id = self.selected_channel_id

        if guild is None or channel_id is None:
            await interaction.response.send_message(
                "Select a sticky message first.",
                ephemeral=True,
            )
            return

        sticky = await get_sticky(guild.id, channel_id)

        if sticky is None:
            await interaction.response.send_message(
                "That sticky no longer exists.",
                ephemeral=True,
            )
            return

        await delete_sticky(guild.id, channel_id)

        channel = guild.get_channel(channel_id)
        last_message_id = sticky.get("last_message_id")

        if (
            last_message_id
            and isinstance(channel, discord.TextChannel)
        ):
            try:
                old_message = await channel.fetch_message(last_message_id)
                await old_message.delete()
            except (
                discord.NotFound,
                discord.Forbidden,
                discord.HTTPException,
            ):
                pass

        self.selected_channel_id = None
        self.stickies = await get_stickies(guild.id)

        self.show_home()

        await interaction.response.edit_message(view=self)


# --------------------------------------------------
# STICKY COMMANDS
# --------------------------------------------------

class Sticky(commands.GroupCog, group_name="sticky"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.sticky_service = StickyService()
        self._channel_locks: dict[int, asyncio.Lock] = {}

    async def interaction_check(
        self,
        interaction: discord.Interaction,
    ) -> bool:
        if interaction.guild is None:
            await interaction.response.send_message(
                "Sticky commands can only be used inside a server.",
                ephemeral=True,
            )
            return False

        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "Unable to verify your server permissions.",
                ephemeral=True,
            )
            return False

        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "You need Administrator permission to manage sticky messages.",
                ephemeral=True,
            )
            return False

        return True

    # --------------------------------------------------
    # ADD
    # --------------------------------------------------

    @app_commands.command(
        name="add",
        description="Add or replace a channel's sticky message.",
    )
    @app_commands.describe(
        channel="The channel where the sticky should appear.",
        message="Text or an embed script.",
    )
    async def add(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        message: str,
    ):
        guild = interaction.guild
        if guild is None:
            return

        try:
            content, embed = self.sticky_service.render(message, guild)

            if not content and embed is None:
                raise ValueError(
                    "The sticky script must contain text or an embed."
                )

            await set_sticky(
                guild_id=guild.id,
                channel_id=channel.id,
                message_script=message,
                created_by=interaction.user.id,
            )

            await interaction.response.send_message(
                f"Sticky message configured for {channel.mention}.",
                ephemeral=True,
            )

        except ValueError as exc:
            await interaction.response.send_message(
                f"Invalid sticky script: {exc}",
                ephemeral=True,
            )

    # --------------------------------------------------
    # LIST
    # --------------------------------------------------

    @app_commands.command(
        name="list",
        description="List and configure sticky messages.",
    )
    async def list_stickies(
        self,
        interaction: discord.Interaction,
    ):
        guild = interaction.guild
        if guild is None:
            return

        stickies = await get_stickies(guild.id)

        view = StickyListView(guild, stickies)

        await interaction.response.send_message(
            view=view,
            ephemeral=True,
        )

    # --------------------------------------------------
    # EDIT
    # --------------------------------------------------

    @app_commands.command(
        name="edit",
        description="Edit a channel's sticky message.",
    )
    @app_commands.describe(
        channel="The channel whose sticky you want to edit.",
        message="The new text or embed script.",
    )
    async def edit(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        message: str,
    ):
        guild = interaction.guild
        if guild is None:
            return

        sticky = await get_sticky(guild.id, channel.id)

        if sticky is None:
            await interaction.response.send_message(
                f"There is no sticky configured for {channel.mention}.",
                ephemeral=True,
            )
            return

        try:
            content, embed = self.sticky_service.render(message, guild)

            if not content and embed is None:
                raise ValueError(
                    "The sticky script must contain text or an embed."
                )

            await set_sticky(
                guild_id=guild.id,
                channel_id=channel.id,
                message_script=message,
                created_by=interaction.user.id,
            )

            await interaction.response.send_message(
                f"Sticky message updated for {channel.mention}.",
                ephemeral=True,
            )

        except ValueError as exc:
            await interaction.response.send_message(
                f"Invalid sticky script: {exc}",
                ephemeral=True,
            )

    # --------------------------------------------------
    # REMOVE
    # --------------------------------------------------

    @app_commands.command(
        name="remove",
        description="Remove a channel's sticky message.",
    )
    @app_commands.describe(
        channel="The channel whose sticky you want to remove.",
    )
    async def remove(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
    ):
        guild = interaction.guild
        if guild is None:
            return

        sticky = await get_sticky(guild.id, channel.id)

        if sticky is None:
            await interaction.response.send_message(
                f"There is no sticky configured for {channel.mention}.",
                ephemeral=True,
            )
            return

        await delete_sticky(guild.id, channel.id)

        last_message_id = sticky.get("last_message_id")

        if last_message_id:
            try:
                old_message = await channel.fetch_message(last_message_id)
                await old_message.delete()
            except (
                discord.NotFound,
                discord.Forbidden,
                discord.HTTPException,
            ):
                pass

        await interaction.response.send_message(
            f"Sticky message removed from {channel.mention}.",
            ephemeral=True,
        )

    # --------------------------------------------------
    # ENABLE / PAUSE
    # --------------------------------------------------

    @app_commands.command(
        name="toggle",
        description="Enable or pause a channel's sticky message.",
    )
    @app_commands.describe(
        channel="The channel whose sticky you want to toggle.",
        enabled="Whether the sticky should be enabled.",
    )
    async def toggle(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        enabled: bool,
    ):
        guild = interaction.guild
        if guild is None:
            return

        updated = await set_sticky_active(
            guild_id=guild.id,
            channel_id=channel.id,
            active=enabled,
        )

        if not updated:
            await interaction.response.send_message(
                f"There is no sticky configured for {channel.mention}.",
                ephemeral=True,
            )
            return

        state = "enabled" if enabled else "paused"

        await interaction.response.send_message(
            f"Sticky message {state} for {channel.mention}.",
            ephemeral=True,
        )

    # --------------------------------------------------
    # MESSAGE LISTENER
    # --------------------------------------------------

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None:
            return

        if not isinstance(message.channel, discord.TextChannel):
            return

        # Kitsu must never trigger its own sticky repost.
        if self.bot.user and message.author.id == self.bot.user.id:
            return

        main_guild_id = getattr(self.bot, "main_guild_id", None)

        if main_guild_id and message.guild.id != main_guild_id:
            return

        lock = self._channel_locks.setdefault(
            message.channel.id,
            asyncio.Lock(),
        )

        async with lock:
            sticky = await get_sticky(
                message.guild.id,
                message.channel.id,
            )

            if not sticky or not sticky.get("active", True):
                return

            ignore_bots = sticky.get("ignore_bots", True)

            if message.author.bot and ignore_bots:
                return

            old_message_id = sticky.get("last_message_id")

            if old_message_id and message.id == old_message_id:
                return

            # Delete the previous sticky before reposting.
            if old_message_id:
                try:
                    old_message = await message.channel.fetch_message(
                        old_message_id
                    )
                    await old_message.delete()

                except discord.NotFound:
                    pass

                except discord.Forbidden:
                    print(
                        "Missing permission to delete sticky in "
                        f"channel {message.channel.id}."
                    )
                    return

                except discord.HTTPException as exc:
                    print(
                        "Failed to delete old sticky in "
                        f"channel {message.channel.id}: {exc}"
                    )
                    return

            await update_sticky_message_id(
                message.guild.id,
                message.channel.id,
                None,
            )

            try:
                new_message = await self.sticky_service.post(
                    message.channel,
                    sticky["message_script"],
                )

                await update_sticky_message_id(
                    message.guild.id,
                    message.channel.id,
                    new_message.id,
                )

            except ValueError as exc:
                print(
                    f"Invalid sticky script in channel "
                    f"{message.channel.id}: {exc}"
                )

            except discord.Forbidden:
                print(
                    "Missing permission to send sticky in "
                    f"channel {message.channel.id}."
                )

            except discord.HTTPException as exc:
                print(
                    f"Failed to repost sticky in channel "
                    f"{message.channel.id}: {exc}"
                )


async def setup(bot: commands.Bot):
    await bot.add_cog(Sticky(bot))
