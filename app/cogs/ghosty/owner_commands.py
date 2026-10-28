from __future__ import annotations

import discord
from discord import app_commands

from app.services.ghosty_tempchannel_service import TempChannelError

from .constants import logger
from .groups import tempchannel


class OwnerCommands:
    @tempchannel.command(
        name="visibility",
        description="Make your temporary channel public or private.",
    )
    @app_commands.choices(
        visibility=[
            app_commands.Choice(name="Public", value="public"),
            app_commands.Choice(name="Private", value="private"),
        ]
    )
    async def tempchannel_visibility(
        self,
        interaction: discord.Interaction,
        visibility: app_commands.Choice[str],
    ) -> None:
        context = await self._get_owned_tempchannel_context(interaction)
        if context is None:
            return

        owner, channel = context

        try:
            await self.tempchannel_service.set_visibility(owner, channel, visibility.value)
        except TempChannelError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        except discord.HTTPException:
            logger.exception("Failed to change temporary-channel visibility")
            await interaction.response.send_message(
                "Discord could not update the channel permissions.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"Your channel is now **{visibility.value}**.",
            ephemeral=True,
        )

    @tempchannel.command(
        name="add",
        description="Give a member access to your temporary channel.",
    )
    async def tempchannel_add(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
    ) -> None:
        context = await self._get_owned_tempchannel_context(interaction)
        if context is None:
            return

        owner, channel = context

        try:
            await self.tempchannel_service.add_member(owner, channel, member)
        except TempChannelError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        except discord.HTTPException:
            logger.exception("Failed to add member to temporary channel")
            await interaction.response.send_message(
                "Discord could not update that member's channel permissions.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"{member.mention} now has access to {channel.mention}.",
            ephemeral=True,
        )

    @tempchannel.command(
        name="remove",
        description="Remove an added member from your temporary channel.",
    )
    async def tempchannel_remove(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
    ) -> None:
        context = await self._get_owned_tempchannel_context(interaction)
        if context is None:
            return

        owner, channel = context

        try:
            await self.tempchannel_service.remove_member(owner, channel, member)
        except TempChannelError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        except discord.HTTPException:
            logger.exception("Failed to remove member from temporary channel")
            await interaction.response.send_message(
                "Discord could not update that member's channel permissions.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"Removed {member.mention} from your temporary channel.",
            ephemeral=True,
        )

    @tempchannel.command(
        name="extend",
        description="Extend the expiry of your temporary channel.",
    )
    @app_commands.choices(
        duration=[
            app_commands.Choice(name="1 hour", value="1h"),
            app_commands.Choice(name="2 hours", value="2h"),
            app_commands.Choice(name="6 hours", value="6h"),
            app_commands.Choice(name="12 hours", value="12h"),
            app_commands.Choice(name="1 day", value="1d"),
            app_commands.Choice(name="3 days", value="3d"),
            app_commands.Choice(name="7 days", value="7d"),
        ]
    )
    async def tempchannel_extend(
        self,
        interaction: discord.Interaction,
        duration: app_commands.Choice[str],
    ) -> None:
        context = await self._get_owned_tempchannel_context(interaction)
        if context is None:
            return

        owner, channel = context

        try:
            expires_at = await self.tempchannel_service.extend_channel(
                owner, channel, duration.value
            )
        except TempChannelError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        timestamp = int(expires_at.timestamp())
        await interaction.response.send_message(
            f"Your channel expiry is now <t:{timestamp}:F> (<t:{timestamp}:R>).",
            ephemeral=True,
        )

    @tempchannel.command(
        name="delete",
        description="Delete your own temporary channel immediately.",
    )
    async def tempchannel_delete(
        self,
        interaction: discord.Interaction,
    ) -> None:
        context = await self._get_owned_tempchannel_context(interaction)
        if context is None:
            return

        owner, channel = context
        channel_name = channel.name

        try:
            await self.tempchannel_service.delete_owned_channel(owner, channel)
        except TempChannelError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        except discord.HTTPException:
            logger.exception("Failed to delete temporary channel")
            await interaction.response.send_message(
                "Discord could not delete the channel. Please try again.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"Your temporary channel **#{channel_name}** has been deleted.",
            ephemeral=True,
        )

    async def _show_tempchannel_creator(
        self,
        interaction: discord.Interaction,
    ) -> None:
        """Open the temporary-channel creation form."""

        if interaction.guild is None:
            await interaction.response.send_message(
                "Use this feature inside a server.",
                ephemeral=True,
            )
            return

        class TempChannelCreateModal(discord.ui.Modal):
            def __init__(modal_self) -> None:
                super().__init__(
                    title="Create Temporary Channel",
                    timeout=300,
                )

                modal_self.channel_name = discord.ui.TextInput(
                    label="Channel name",
                    placeholder="e.g. gaming-lounge",
                    min_length=1,
                    max_length=100,
                    required=True,
                )

                modal_self.add_item(modal_self.channel_name)

            async def on_submit(
                modal_self,
                submit_interaction: discord.Interaction,
            ) -> None:
                duration_view = TempChannelDurationView(
                    submit_interaction.user,
                    modal_self.channel_name.value.strip(),
                )

                await submit_interaction.response.send_message(
                    "Choose how long the channel should exist.",
                    view=duration_view,
                    ephemeral=True,
                )

                duration_view.prompt_message = await submit_interaction.original_response()

        class TempChannelDurationView(discord.ui.View):
            def __init__(
                view_self,
                user: discord.abc.User,
                channel_name: str,
            ) -> None:
                super().__init__(timeout=180)
                view_self.user_id = user.id
                view_self.channel_name = channel_name
                view_self.prompt_message = None

                options = [
                    discord.SelectOption(label="1 hour", value="1h"),
                    discord.SelectOption(label="2 hours", value="2h"),
                    discord.SelectOption(label="6 hours", value="6h"),
                    discord.SelectOption(label="12 hours", value="12h"),
                    discord.SelectOption(label="1 day", value="1d"),
                    discord.SelectOption(label="3 days", value="3d"),
                    discord.SelectOption(label="7 days", value="7d"),
                ]

                select = discord.ui.Select(
                    placeholder="Select duration",
                    min_values=1,
                    max_values=1,
                    options=options,
                )
                select.callback = view_self.create_channel
                view_self.add_item(select)

            async def create_channel(
                view_self,
                select_interaction: discord.Interaction,
            ) -> None:
                if select_interaction.user.id != view_self.user_id:
                    await select_interaction.response.send_message(
                        "This creation form belongs to another member.",
                        ephemeral=True,
                    )
                    return

                member = select_interaction.user
                if not isinstance(member, discord.Member):
                    await select_interaction.response.send_message(
                        "I couldn't identify your server membership.",
                        ephemeral=True,
                    )
                    return

                duration_key = select_interaction.data["values"][0]

                await select_interaction.response.defer(ephemeral=True)

                try:
                    channel = await self.tempchannel_service.create_channel(
                        member=member,
                        name=view_self.channel_name,
                        duration_key=duration_key,
                    )
                except TempChannelError as exc:
                    await select_interaction.followup.send(
                        str(exc),
                        ephemeral=True,
                    )
                    return
                except discord.HTTPException:
                    logger.exception("Failed to create a Ghosty temporary channel.")
                    await select_interaction.followup.send(
                        "Discord couldn't create the channel. Please try again.",
                        ephemeral=True,
                    )
                    return

                if view_self.prompt_message is not None:
                    try:
                        await view_self.prompt_message.delete()
                    except discord.NotFound:
                        pass
                    except discord.HTTPException:
                        logger.exception("Could not delete the temporary-channel duration prompt.")

                await select_interaction.followup.send(
                    f"Your temporary channel {channel.mention} is ready!\n"
                    "Use your server prefix with `ghosty tempchannel settings` "
                    "to open its controls.",
                    ephemeral=True,
                )

        await interaction.response.send_modal(TempChannelCreateModal())
