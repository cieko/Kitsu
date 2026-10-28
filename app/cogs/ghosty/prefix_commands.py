from __future__ import annotations

import discord
from discord.ext import commands

from app.database.repositories import ghosty_pause_repository as pause_repository
from app.services.ghosty_pause_service import PauseError, is_pause_moderator
from app.services.ghosty_pause_ui import GhostyPauseView, PauseSetupView
from app.services.ghosty_prediction import utc
from app.services.ghosty_streak_ui import GhostyStreakView
from app.services.ghosty_tempchannel_service import TempChannelError

from .constants import TEMPCHANNEL_SETTINGS_THUMBNAIL, logger
from .tempchannel_settings import GhostyTempChannelSettingsView


class PrefixCommands:
    @commands.group(
        name="ghosty",
        invoke_without_command=True,
    )
    async def ghosty_text(
        self,
        ctx: commands.Context,
    ) -> None:
        """Prefix-command entry point for Ghosty."""
        await ctx.send(
            f"Use `{ctx.clean_prefix}ghosty streak` for this channel’s gambling history, "
            f"`{ctx.clean_prefix}ghosty pause` to reserve a dashboard channel, "
            f"or `{ctx.clean_prefix}ghosty tempchannel settings` inside your own active temporary channel."
        )

    @ghosty_text.command(name="streak")
    async def ghosty_streak_text(self, ctx: commands.Context) -> None:
        """Show previous hit attempts and the current streak; available to members."""
        if ctx.guild is None:
            await ctx.send("Use this command inside a server channel.")
            return
        try:
            async with ctx.typing():
                snapshot = await self.streak_service.load(ctx.guild.id, ctx.channel.id)
                view = GhostyStreakView(
                    service=self.streak_service,
                    snapshot=snapshot,
                    owner_id=ctx.author.id,
                    guild_id=ctx.guild.id,
                    channel_id=ctx.channel.id,
                    prefix=ctx.clean_prefix,
                    can_attach_files=ctx.channel.permissions_for(ctx.me).attach_files,
                )
                chart = await view.chart_file()
                view.message = await ctx.send(
                    view=view,
                    allowed_mentions=discord.AllowedMentions.none(),
                    **({"file": chart} if chart is not None else {}),
                )
        except Exception:
            logger.exception("Could not open Ghosty streak history in channel %s.", ctx.channel.id)
            await ctx.send("I couldn’t load this channel’s gambling history. Please try again.")

    @ghosty_text.command(name="pause")
    async def ghosty_pause_text(self, ctx: commands.Context) -> None:
        """Reserve a dashboard channel after five verified gambles in its current streak."""
        if (
            ctx.guild is None
            or not isinstance(ctx.channel, discord.TextChannel)
            or not isinstance(ctx.author, discord.Member)
        ):
            await ctx.send("Use this command inside a server text channel.")
            return
        try:
            record = await pause_repository.get_pause(ctx.guild.id, ctx.channel.id)
            if record is not None:
                if str(ctx.author.id) != record["owner_id"] and not is_pause_moderator(ctx.author):
                    await ctx.send(
                        f"This channel is reserved by <@{record['owner_id']}> until <t:{int(utc(record['expires_at']).timestamp())}:R>.",
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                    return
                if record.get("panel_id"):
                    try:
                        panel = await ctx.channel.fetch_message(int(record["panel_id"]))
                        await panel.edit(
                            view=GhostyPauseView(
                                self, record, guild_id=ctx.guild.id, channel_id=ctx.channel.id
                            )
                        )
                        await ctx.send(f"Pause controls: {panel.jump_url}")
                        return
                    except discord.NotFound:
                        pass
                panel = await ctx.send(
                    view=GhostyPauseView(
                        self, record, guild_id=ctx.guild.id, channel_id=ctx.channel.id
                    ),
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                await self.pause_service.set_panel(
                    ctx.channel, ctx.author, pause_id=record["id"], panel_id=panel.id
                )
                return
            count = await self.pause_service.eligible_count(ctx.channel, ctx.author)
            if count < 5:
                raise PauseError(
                    f"You need 5 verified gambles in this channel’s current streak; I can verify {count}."
                )
            await ctx.send(
                view=PauseSetupView(self, ctx.author, ctx.channel, count=count),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except PauseError as error:
            await ctx.send(str(error))
        except discord.HTTPException:
            await ctx.send(
                "Discord could not read or update this channel. Check my channel permissions and try again."
            )
        except Exception:
            logger.exception("Could not open Ghosty pause controls in channel %s.", ctx.channel.id)
            await ctx.send("I couldn’t load this channel’s pause controls. Please try again.")

    @ghosty_text.command(name="resume")
    async def ghosty_resume_text(self, ctx: commands.Context) -> None:
        if (
            ctx.guild is None
            or not isinstance(ctx.channel, discord.TextChannel)
            or not isinstance(ctx.author, discord.Member)
        ):
            await ctx.send("Use this command inside a server text channel.")
            return
        try:
            await self.pause_service.resume(ctx.channel, ctx.author)
            await ctx.send(
                "Channel resumed. The pause’s original writing permissions have been restored."
            )
        except PauseError as error:
            await ctx.send(str(error))
        except discord.HTTPException:
            await ctx.send(
                "Permissions could not be restored yet. Automatic restoration will retry."
            )
        except Exception:
            logger.exception("Could not resume Ghosty channel %s.", ctx.channel.id)
            await ctx.send(
                "I couldn’t complete the resume request. Pending permission restoration will retry automatically."
            )

    @ghosty_text.group(
        name="tempchannel",
        invoke_without_command=True,
    )
    async def ghosty_tempchannel_text(
        self,
        ctx: commands.Context,
    ) -> None:
        await ctx.send(
            f"Use `{ctx.clean_prefix}ghosty tempchannel settings` to open your channel settings."
        )

    @ghosty_tempchannel_text.command(
        name="settings",
    )
    async def ghosty_tempchannel_settings_text(
        self,
        ctx: commands.Context,
    ) -> None:
        """Open the settings panel for the current owned temp channel."""

        if ctx.guild is None or not isinstance(ctx.author, discord.Member):
            await ctx.send("Use this command inside the server.")
            return

        if not isinstance(ctx.channel, discord.TextChannel):
            await ctx.send("Use this command inside your temporary text channel.")
            return

        try:
            await self.tempchannel_service._get_owned_channel(
                ctx.author,
                ctx.channel,
            )
            await self.tempchannel_service.ensure_command_permissions(ctx.channel)
        except TempChannelError as exc:
            await ctx.send(str(exc))
            return
        except discord.HTTPException:
            logger.exception("Could not apply temporary-channel command permissions")
            await ctx.send(
                "I couldn't update this channel's command permissions. "
                "Check my permission to manage channel permissions and try again."
            )
            return

        thumbnail_url = str(ctx.guild.icon.url) if ctx.guild.icon else None
        has_custom_thumbnail = TEMPCHANNEL_SETTINGS_THUMBNAIL.is_file()
        if has_custom_thumbnail:
            thumbnail_url = f"attachment://{TEMPCHANNEL_SETTINGS_THUMBNAIL.name}"

        view = GhostyTempChannelSettingsView(
            cog=self,
            owner_id=ctx.author.id,
            channel_id=ctx.channel.id,
            server_name=ctx.guild.name,
            thumbnail_url=thumbnail_url,
        )
        if has_custom_thumbnail:
            thumbnail = discord.File(
                TEMPCHANNEL_SETTINGS_THUMBNAIL,
                filename=TEMPCHANNEL_SETTINGS_THUMBNAIL.name,
            )
            try:
                await ctx.send(
                    view=view,
                    file=thumbnail,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            finally:
                thumbnail.close()
        else:
            await ctx.send(view=view, allowed_mentions=discord.AllowedMentions.none())
