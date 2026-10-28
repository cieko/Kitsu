from __future__ import annotations

import asyncio

import discord

from .components import (
    MakePublicButton,
    StealActionButton,
    StealExportSelect,
    build_asset_container,
)
from .constants import ASSET_COLOR, STEAL_TIMEOUT, logger
from .models import StealItem


class StealEmojiView(discord.ui.LayoutView):
    def __init__(
        self,
        items: list[StealItem],
        owner_id: int,
        is_slash: bool = False,
    ):
        super().__init__(timeout=None)

        self.items = items
        self.owner_id = owner_id
        self.is_slash = is_slash
        self.page = 0

        self.message: discord.Message | discord.InteractionMessage | None = None

        self.expiry_task: asyncio.Task | None = None
        self.inactive = False

        self.render_page()

    def render_page(self) -> None:
        self.clear_items()

        item = self.items[self.page]
        total = len(self.items)

        container = build_asset_container(
            item,
            self.page,
            total,
        )

        export_select = StealExportSelect(
            self,
            [
                discord.SelectOption(
                    label=fmt,
                    value=fmt,
                    description=f"Export as {fmt}",
                )
                for fmt in item.export_options
            ],
        )

        container.add_item(discord.ui.ActionRow(export_select))

        previous = StealActionButton(
            self,
            "previous",
            label="Previous",
            disabled=self.page == 0,
        )

        next_button = StealActionButton(
            self,
            "next",
            label="Next",
            disabled=self.page >= total - 1,
        )

        container.add_item(discord.ui.ActionRow(previous, next_button))

        # Main asset preview: shown for both slash and prefix commands.
        self.add_item(container)

        # Privacy note + Make Public: shown only for slash commands.
        if self.is_slash:
            self.add_item(
                discord.ui.Container(
                    discord.ui.Section(
                        discord.ui.TextDisplay("*_Note: Only you can view this emoji preview_*"),
                        accessory=MakePublicButton(self),
                    ),
                    accent_colour=ASSET_COLOR,
                )
            )

    async def interaction_check(
        self,
        interaction: discord.Interaction,
    ) -> bool:
        if self.inactive:
            await interaction.response.send_message(
                "This steal menu is now inactive. Run the command again.",
                ephemeral=True,
            )
            return False

        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "Only the person who ran this command can use these controls.",
                ephemeral=True,
            )
            return False

        return True

    def start_expiry_timer(self) -> None:
        if self.expiry_task:
            self.expiry_task.cancel()

        self.expiry_task = asyncio.create_task(self._expire_after_timeout())

    async def _expire_after_timeout(self) -> None:
        try:
            await asyncio.sleep(STEAL_TIMEOUT)
            await self.expire_view()
        except asyncio.CancelledError:
            pass

    async def expire_view(self) -> None:
        if self.inactive:
            return

        self.inactive = True
        self.clear_items()

        self.add_item(discord.ui.TextDisplay("-# The message is now inactive."))

        item = self.items[self.page]

        container = build_asset_container(
            item,
            self.page,
            len(self.items),
        )

        # Disable the Steal button when the menu expires.
        for child in container.children:
            if isinstance(child, discord.ui.Section):
                accessory = getattr(child, "accessory", None)

                if isinstance(accessory, discord.ui.Button):
                    accessory.disabled = True

        export_select = StealExportSelect(
            self,
            [
                discord.SelectOption(
                    label=fmt,
                    value=fmt,
                    description=f"Export as {fmt}",
                )
                for fmt in item.export_options
            ],
        )
        export_select.disabled = True

        container.add_item(discord.ui.ActionRow(export_select))

        container.add_item(
            discord.ui.ActionRow(
                StealActionButton(
                    self,
                    "previous",
                    label="Previous",
                    disabled=True,
                ),
                StealActionButton(
                    self,
                    "next",
                    label="Next",
                    disabled=True,
                ),
            )
        )

        self.add_item(container)

        # Keep the second container conditional after timeout as well.
        if self.is_slash:
            public_button = MakePublicButton(self)
            public_button.disabled = True

            self.add_item(
                discord.ui.Container(
                    discord.ui.Section(
                        discord.ui.TextDisplay("*`_Note: Only you can view this emoji preview_`*"),
                        accessory=public_button,
                    ),
                    accent_colour=ASSET_COLOR,
                )
            )

        if self.message is not None:
            try:
                await self.message.edit(view=self)
                logger.info("[STEAL] UI expired and controls disabled.")
            except discord.HTTPException:
                logger.exception("[STEAL] Could not edit expired UI.")
