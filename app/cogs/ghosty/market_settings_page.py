from __future__ import annotations

import asyncio

import discord

from app.database.repositories import ghosty_repository as repository


async def build_settings_page(
    self,
    guild,
    channels,
    excluded_channel_ids,
    channels_per_page,
    total_pages,
    page: int,
) -> discord.ui.LayoutView:
    async def build_settings_view(page):
        return await build_settings_page(
            self, guild, channels, excluded_channel_ids, channels_per_page, total_pages, page
        )

    page = max(0, min(page, total_pages - 1))

    settings_view = discord.ui.LayoutView(timeout=300)

    settings_container = discord.ui.Container(
        accent_colour=discord.Colour.dark_purple(),
    )

    settings_container.add_item(
        discord.ui.TextDisplay(
            content=(
                "## Ghosty Market Settings\n"
                "Toggle each text channel to choose whether it "
                "appears on the market dashboard.\n\n"
                "✅ **Included** — shown on the dashboard\n"
                "🚫 **Excluded** — hidden from the dashboard\n\n"
                "These settings never delete historical records."
            )
        )
    )

    settings_container.add_item(discord.ui.Separator())

    start_index = page * channels_per_page
    page_channels = channels[start_index : start_index + channels_per_page]

    if not channels:
        settings_container.add_item(
            discord.ui.TextDisplay(content="No accessible text channels were found.")
        )

    # Render up to five channel buttons per row.
    for row_start in range(0, len(page_channels), 5):
        channel_row = discord.ui.ActionRow()

        for channel in page_channels[row_start : row_start + 5]:
            is_excluded = channel.id in excluded_channel_ids

            marker = "🚫" if is_excluded else "✅"
            label = f"{marker} {channel.name}"[:80]

            channel_button = discord.ui.Button(
                label=label,
                style=(discord.ButtonStyle.danger if is_excluded else discord.ButtonStyle.success),
                custom_id=(f"ghosty:settings:toggle:{channel.id}"),
            )

            async def toggle_channel(
                button_interaction: discord.Interaction,
                channel_id: int = channel.id,
                current_page: int = page,
            ) -> None:
                if button_interaction.guild_id != guild.id:
                    await button_interaction.response.send_message(
                        "This settings panel belongs to another server.",
                        ephemeral=True,
                    )
                    return

                if channel_id in excluded_channel_ids:
                    excluded_channel_ids.remove(channel_id)
                else:
                    excluded_channel_ids.add(channel_id)

                updated_view = await build_settings_view(current_page)

                await button_interaction.response.edit_message(view=updated_view)

            channel_button.callback = toggle_channel
            channel_row.add_item(channel_button)

        settings_container.add_item(channel_row)

    settings_container.add_item(discord.ui.Separator())

    navigation_row = discord.ui.ActionRow()

    previous_button = discord.ui.Button(
        label="Previous",
        style=discord.ButtonStyle.secondary,
        disabled=(page == 0),
    )

    page_indicator = discord.ui.Button(
        label=f"Page {page + 1}/{total_pages}",
        style=discord.ButtonStyle.secondary,
        disabled=True,
    )

    next_button = discord.ui.Button(
        label="Next",
        style=discord.ButtonStyle.secondary,
        disabled=(page >= total_pages - 1),
    )

    async def go_previous(
        page_interaction: discord.Interaction,
    ) -> None:
        updated_view = await build_settings_view(page - 1)
        await page_interaction.response.edit_message(view=updated_view)

    async def go_next(
        page_interaction: discord.Interaction,
    ) -> None:
        updated_view = await build_settings_view(page + 1)
        await page_interaction.response.edit_message(view=updated_view)

    previous_button.callback = go_previous
    next_button.callback = go_next

    navigation_row.add_item(previous_button)
    navigation_row.add_item(page_indicator)
    navigation_row.add_item(next_button)

    settings_container.add_item(navigation_row)

    actions_row = discord.ui.ActionRow()

    save_button = discord.ui.Button(
        label="Save changes",
        style=discord.ButtonStyle.primary,
    )

    restore_button = discord.ui.Button(
        label="Restore all",
        style=discord.ButtonStyle.secondary,
    )

    async def save_exclusions(
        save_interaction: discord.Interaction,
    ) -> None:
        current_exclusions = {
            int(channel_id)
            for channel_id in await repository.get_config(
                guild.id,
                "dashboard_excluded_channels",
                [],
            )
        }

        accessible_ids = {channel.id for channel in channels}

        # Preserve exclusions for inaccessible channels.
        inaccessible_exclusions = current_exclusions - accessible_ids

        saved_exclusions = inaccessible_exclusions | (excluded_channel_ids & accessible_ids)

        await repository.set_config(
            guild.id,
            "dashboard_excluded_channels",
            sorted(saved_exclusions),
        )

        # Acknowledge before performing other operations.
        await save_interaction.response.defer(ephemeral=True)

        # Delete the temporary settings panel.
        try:
            await save_interaction.delete_original_response()
        except (
            discord.NotFound,
            discord.HTTPException,
        ):
            pass

        # Show confirmation immediately.
        await save_interaction.followup.send(
            (
                "Ghosty Market settings saved.\n"
                f"**Excluded channels:** {len(saved_exclusions)}\n"
                "The dashboard will be reposted shortly. "
                "Historical records have not been changed."
            ),
            ephemeral=True,
        )

        # Keep the existing 3-second confirmation delay.
        await asyncio.sleep(3)

        if not await self._refresh_dashboard_for_guild(guild, repost=True):
            await save_interaction.followup.send(
                "Your settings were saved, but I couldn't repost the dashboard. "
                "Check my channel permissions and try again.",
                ephemeral=True,
            )

    async def restore_all(
        restore_interaction: discord.Interaction,
    ) -> None:
        if restore_interaction.guild_id != guild.id:
            await restore_interaction.response.send_message(
                "This settings panel belongs to another server.",
                ephemeral=True,
            )
            return

        excluded_channel_ids.clear()

        await repository.set_config(
            guild.id,
            "dashboard_excluded_channels",
            [],
        )

        updated_view = await build_settings_view(page)

        await restore_interaction.response.edit_message(view=updated_view)

        reposted = await self._refresh_dashboard_for_guild(guild, repost=True)
        await restore_interaction.followup.send(
            "All channels have been restored to the Ghosty Market. "
            + (
                "The dashboard has been reposted."
                if reposted
                else "I couldn't repost the dashboard; check my channel permissions."
            ),
            ephemeral=True,
        )

    save_button.callback = save_exclusions
    restore_button.callback = restore_all

    actions_row.add_item(save_button)
    actions_row.add_item(restore_button)

    settings_container.add_item(actions_row)
    settings_view.add_item(settings_container)

    return settings_view
