"""Duration selection and persistent Components V2 pause controls."""

from __future__ import annotations

import logging

import discord

from app.services.ghosty_pause_service import PAUSE_DURATIONS, PAUSE_MOD_ROLE_ID, PauseError
from app.services.ghosty_prediction import utc

logger = logging.getLogger(__name__)


class PauseView(discord.ui.LayoutView):
    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item) -> None:
        logger.error("Ghosty pause control failed.", exc_info=(type(error), error, error.__traceback__))
        message = "I couldn’t complete that pause action. Please try again; pending permission restoration will retry automatically."
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


class PauseSetupView(PauseView):
    def __init__(self, cog, member: discord.Member, channel: discord.TextChannel, *, count: int):
        super().__init__(timeout=180)
        self.cog, self.owner_id, self.channel_id, self.guild_id = cog, member.id, channel.id, channel.guild.id
        select = discord.ui.Select(
            custom_id="ghosty:pause:choose_duration", placeholder="Choose how long to reserve this channel…",
            options=[discord.SelectOption(label=f"{key[:-1]} {'minutes' if key.endswith('m') else ('hour' if key == '1h' else 'hours')}", value=key)
                     for key in PAUSE_DURATIONS],
        )

        async def choose(interaction: discord.Interaction):
            await interaction.response.defer()
            try:
                await cog.pause_service.reserve(
                    interaction.channel, interaction.user, select.values[0], panel_id=interaction.message.id,
                )
                self.stop()
            except PauseError as error:
                await interaction.followup.send(str(error), ephemeral=True)
            except discord.HTTPException:
                await interaction.followup.send("Discord could not update this channel’s permissions. Please try again.", ephemeral=True)

        select.callback = choose
        self.add_item(discord.ui.Container(
            discord.ui.Section(
                discord.ui.TextDisplay(
                    f"### {discord.utils.escape_markdown(channel.guild.name)}\n**Reserve #{discord.utils.escape_markdown(channel.name)}**"
                ),
                accessory=discord.ui.Button(label="Open channel", style=discord.ButtonStyle.link,
                                            url=f"https://discord.com/channels/{self.guild_id}/{self.channel_id}"),
            ),
            discord.ui.TextDisplay(
                f"<@{member.id}> has **{count} verified gambles** in the current streak.\n\n"
                "Choose a duration from **5 minutes to 12 hours** to make the channel read-only for other members. "
                "You, approved members, moderators, Kitsu, and Ghosty can keep sending messages.\n\n"
                "The five-gamble requirement is checked again when you choose. Permissions restore automatically at expiry."
            ),
            discord.ui.ActionRow(select), accent_colour=0x7CC6B0,
        ))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if (interaction.user.id != self.owner_id or interaction.guild is None
                or interaction.guild.id != self.guild_id or interaction.channel_id != self.channel_id):
            await interaction.response.send_message("Only the requesting member can select a duration in this channel.", ephemeral=True)
            return False
        return True


class GhostyPauseView(PauseView):
    def __init__(self, cog, record: dict, *, guild_id: int, channel_id: int, closed: bool = False):
        super().__init__(timeout=None)
        self.cog, self.record, self.guild_id, self.channel_id = cog, record, guild_id, channel_id
        self.pause_id, self.closed = record["id"], closed
        channel = cog.bot.get_channel(channel_id)
        channel_name = discord.utils.escape_markdown(channel.name if channel else str(channel_id))
        active = not closed and record["state"] == "active"
        expiry = int(utc(record["expires_at"]).timestamp())
        approved = " ".join(f"<@{member_id}>" for member_id in record["allowed_ids"]) or "No additional members"
        if closed:
            status = "**Channel resumed.** The pause’s original writing permissions have been restored."
        elif active:
            status = f"**Read-only reservation** · Ends <t:{expiry}:R>\n**Expires:** <t:{expiry}:F> · **Duration:** {record['duration']}"
        else:
            status = "**Permission restoration is pending.** It will retry automatically; the owner or a moderator can also press Resume."
        add = discord.ui.UserSelect(custom_id="ghosty:pause:add", placeholder="Allow a member to send messages…", disabled=not active)
        remove = discord.ui.UserSelect(custom_id="ghosty:pause:remove", placeholder="Remove an approved member…",
                                       disabled=not active or not record["allowed_ids"])
        resume = discord.ui.Button(label="Resume channel", custom_id="ghosty:pause:resume",
                                   style=discord.ButtonStyle.danger, disabled=closed)

        async def add_member(interaction: discord.Interaction):
            await self._member_action(interaction, add.values[0], add=True)

        async def remove_member(interaction: discord.Interaction):
            await self._member_action(interaction, remove.values[0], add=False)

        async def resume_channel(interaction: discord.Interaction):
            await interaction.response.defer()
            try:
                await cog.pause_service.resume(interaction.channel, interaction.user, pause_id=self.pause_id)
            except PauseError as error:
                await interaction.followup.send(str(error), ephemeral=True)
            except discord.HTTPException:
                await interaction.followup.send("Discord could not restore permissions yet. Automatic restoration will retry.", ephemeral=True)

        add.callback, remove.callback, resume.callback = add_member, remove_member, resume_channel
        self.add_item(discord.ui.Container(
            discord.ui.Section(
                discord.ui.TextDisplay(f"### Ghosty Channel Pause\n**#{channel_name}**"),
                accessory=discord.ui.Button(label="Open channel", style=discord.ButtonStyle.link,
                                            url=f"https://discord.com/channels/{guild_id}/{channel_id}"),
            ),
            discord.ui.TextDisplay(f"{status}\n\n**Owner:** <@{record['owner_id']}>\n**Moderators:** <@&{PAUSE_MOD_ROLE_ID}>\n"
                                   f"**Approved members:** {approved}"),
            discord.ui.Separator(), discord.ui.ActionRow(add), discord.ui.ActionRow(remove), discord.ui.ActionRow(resume),
            discord.ui.TextDisplay("-# Only the owner and moderators can manage access. Gambling and tracking continue."),
            accent_colour=0x7CC6B0,
        ))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if (self.closed or interaction.guild is None or interaction.guild.id != self.guild_id
                or interaction.channel_id != self.channel_id):
            await interaction.response.send_message("Use the active pause controls in their original channel.", ephemeral=True)
            return False
        # The service rechecks the stored pause ID, owner, roles, and expiry on
        # every action, including callbacks restored after a bot restart.
        return True

    async def _member_action(self, interaction: discord.Interaction, target: discord.Member, *, add: bool):
        await interaction.response.defer()
        if not isinstance(target, discord.Member):
            await interaction.followup.send("Choose a member of this server.", ephemeral=True)
            return
        try:
            await self.cog.pause_service.change_member(
                interaction.channel, interaction.user, target, pause_id=self.pause_id, add=add,
            )
        except PauseError as error:
            await interaction.followup.send(str(error), ephemeral=True)
        except discord.HTTPException:
            await interaction.followup.send("Discord could not update member access. Please try again.", ephemeral=True)
