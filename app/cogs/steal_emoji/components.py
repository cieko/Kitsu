from __future__ import annotations

from typing import TYPE_CHECKING

from .constants import ASSET_COLOR, logger
from .export import download_file, export_item
from .models import StealItem

if TYPE_CHECKING:
    from .view import StealEmojiView
import io
import re
from typing import Literal

import discord

from app.bot.permissions import require_guild_permission


class StealToServerButton(discord.ui.Button):
    def __init__(self, item: StealItem):
        super().__init__(
            label="Steal",
            style=discord.ButtonStyle.blurple,
            emoji="🍥",
        )
        self.item = item

    async def callback(self, interaction: discord.Interaction) -> None:
        item = self.item
        guild = interaction.guild

        if guild is None:
            await interaction.response.send_message(
                "This can only be used inside a server.",
                ephemeral=True,
            )
            return

        if item.item_type != "emoji":
            await interaction.response.send_message(
                "The Steal button currently supports custom emojis only.",
                ephemeral=True,
            )
            return

        if not await require_guild_permission(
            interaction, "create_expressions", "Create Expressions"
        ):
            return

        bot_member = guild.me

        if bot_member is None or not (
            bot_member.guild_permissions.administrator
            or bot_member.guild_permissions.create_expressions
        ):
            await interaction.response.send_message(
                "I need the **Create Expressions** permission.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        try:
            image_bytes = await download_file(item.url)

            emoji = await guild.create_custom_emoji(
                name=item.name[:32],
                image=image_bytes,
                reason=f"Emoji added by {interaction.user}",
            )

            await interaction.followup.send(
                f"Successfully added {emoji} (`{emoji.name}`) to **{guild.name}**!",
                ephemeral=True,
            )

        except discord.HTTPException as exc:
            logger.exception(
                "[STEAL] Could not add emoji | item_id=%s",
                item.item_id,
            )
            await interaction.followup.send(
                f"Discord couldn't add this emoji: {exc}",
                ephemeral=True,
            )

        except Exception:
            logger.exception(
                "[STEAL] Failed to add emoji | item_id=%s",
                item.item_id,
            )
            await interaction.followup.send(
                "Something went wrong while adding the emoji.",
                ephemeral=True,
            )


def build_asset_container(
    item: StealItem,
    page: int,
    total: int,
) -> discord.ui.Container:
    title = "Emoji" if item.item_type == "emoji" else "Sticker"

    parts: list[discord.ui.Item] = [
        discord.ui.Section(
            discord.ui.TextDisplay(
                f"### [Enlarged {title}]({item.url})\n"
                f"-# `{item.item_type}` `{item.name}` `{item.item_id}`"
            ),
            accessory=StealToServerButton(item),
        ),
    ]

    if item.source_format == "lottie":
        parts.append(discord.ui.TextDisplay(f"[Open sticker preview]({item.url})"))
    else:
        parts.append(discord.ui.MediaGallery(discord.MediaGalleryItem(item.url)))

    parts.extend(
        [
            discord.ui.Separator(),
            discord.ui.TextDisplay(f"-# Page **{page + 1}/{total}**"),
        ]
    )

    return discord.ui.Container(
        *parts,
        accent_colour=ASSET_COLOR,
    )


class StealActionButton(discord.ui.Button):
    def __init__(
        self,
        parent_view: StealEmojiView,
        action: Literal["previous", "next"],
        *,
        label: str,
        disabled: bool = False,
    ):
        super().__init__(
            label=label,
            style=discord.ButtonStyle.secondary,
            disabled=disabled,
        )

        self.parent_view = parent_view
        self.action = action

    async def callback(
        self,
        interaction: discord.Interaction,
    ) -> None:
        view = self.parent_view

        if view.inactive:
            await interaction.response.send_message(
                "This steal menu is now inactive. Run the command again.",
                ephemeral=True,
            )
            return

        if self.action == "previous":
            view.page -= 1
        else:
            view.page += 1

        view.render_page()
        await interaction.response.edit_message(view=view)


class MakePublicButton(discord.ui.Button):
    def __init__(self, parent_view: StealEmojiView):
        super().__init__(
            label="Make Public",
            style=discord.ButtonStyle.primary,
        )
        self.parent_view = parent_view

    async def callback(
        self,
        interaction: discord.Interaction,
    ) -> None:
        view = self.parent_view

        if view.inactive:
            await interaction.response.send_message(
                "This steal menu is now inactive. Run the command again.",
                ephemeral=True,
            )
            return

        channel = interaction.channel

        if channel is None or not hasattr(channel, "send"):
            await interaction.response.send_message(
                "I couldn't publish the preview in this channel.",
                ephemeral=True,
            )
            return

        item = view.items[view.page]
        public_view = discord.ui.LayoutView(timeout=None)

        public_view.add_item(
            build_asset_container(
                item,
                view.page,
                len(view.items),
            )
        )

        try:
            await channel.send(view=public_view)
        except discord.HTTPException:
            logger.exception(
                "[STEAL] Could not publish asset | item_id=%s",
                item.item_id,
            )
            await interaction.response.send_message(
                "I couldn't publish the preview. Check the bot's channel permissions.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"Published **{item.name}** in this channel.",
            ephemeral=True,
        )


class StealExportSelect(discord.ui.Select):
    def __init__(
        self,
        parent_view: StealEmojiView,
        options: list[discord.SelectOption],
    ):
        super().__init__(
            placeholder="Export as",
            min_values=1,
            max_values=1,
            options=options,
        )
        self.parent_view = parent_view

    async def callback(
        self,
        interaction: discord.Interaction,
    ) -> None:
        view = self.parent_view

        if view.inactive:
            await interaction.response.send_message(
                "This steal menu is now inactive. Run the command again.",
                ephemeral=True,
            )
            return

        item = view.items[view.page]
        selected_format = self.values[0]

        await interaction.response.defer(
            ephemeral=True,
            thinking=True,
        )

        try:
            data, extension = await export_item(
                item,
                selected_format,
            )

            safe_name = re.sub(
                r"[^A-Za-z0-9_-]",
                "_",
                item.name,
            )[:50]

            filename = f"{safe_name or 'discord_asset'}.{extension}"

            await interaction.followup.send(
                content=(f"Exported **{item.name}** as **{selected_format}**."),
                file=discord.File(
                    io.BytesIO(data),
                    filename=filename,
                ),
                ephemeral=True,
            )

        except Exception:
            logger.exception(
                "[STEAL] Export failed | item_id=%s | format=%s",
                item.item_id,
                selected_format,
            )
            await interaction.followup.send(
                "Export failed. Check the bot console logs for details.",
                ephemeral=True,
            )
