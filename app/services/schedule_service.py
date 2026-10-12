import re

from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import discord
from discord.ext import commands, tasks

from app.database.repositories.schedule_repository import (
    get_active_schedules,
    get_timezone,
    mark_schedule_sent,
)


# --------------------------------------------------
# MESSAGE SCRIPT PARSER
# --------------------------------------------------

DIRECTIVE_PATTERN = re.compile(
    r"\{(embed|content|color|title|description|footer|thumbnail|image)"
    r"\s*(?::\s*|\})",
    re.IGNORECASE,
)


def parse_message_script(script: str):
    matches = list(DIRECTIVE_PATTERN.finditer(script))

    # Plain text without directives.
    if not matches:
        content = script.strip()

        if len(content) > 2000:
            raise ValueError(
                "Message content cannot exceed 2000 characters."
            )

        return content, None

    content_parts = []
    fields = {}
    embed_requested = False

    # Preserve text before the first directive.
    prefix = script[:matches[0].start()].strip()

    if prefix:
        content_parts.append(prefix)

    for index, match in enumerate(matches):
        key = match.group(1).lower()

        # Each directive's value ends at the next directive.
        end = (
            matches[index + 1].start()
            if index + 1 < len(matches)
            else len(script)
        )

        value = script[match.end():end].strip()

        # Remove the closing brace for this directive.
        if value.endswith("}"):
            value = value[:-1].rstrip()

        if key == "embed":
            embed_requested = True

        elif key == "content":
            if value:
                content_parts.append(value)

        else:
            fields[key] = value

    content = "\n".join(content_parts).strip()

    if len(content) > 2000:
        raise ValueError(
            "Message content cannot exceed 2000 characters."
        )

    embed_fields = {
        "color",
        "title",
        "description",
        "footer",
        "thumbnail",
        "image",
    }

    embed = None

    if embed_requested or any(
        key in fields for key in embed_fields
    ):
        embed = discord.Embed()

        if fields.get("color"):
            try:
                embed.color = discord.Color.from_str(
                    fields["color"]
                )
            except (ValueError, TypeError) as exc:
                raise ValueError(
                    "Invalid embed color. Use a color such as #73052B."
                ) from exc

        if fields.get("title"):
            embed.title = fields["title"]

        if fields.get("description"):
            embed.description = fields["description"]

        if fields.get("footer"):
            embed.set_footer(text=fields["footer"])

        if fields.get("thumbnail"):
            embed.set_thumbnail(url=fields["thumbnail"])

        if fields.get("image"):
            embed.set_image(url=fields["image"])

        # Validate embed field lengths.
        if embed.title and len(embed.title) > 256:
            raise ValueError(
                "Embed title cannot exceed 256 characters."
            )

        if embed.description and len(embed.description) > 4096:
            raise ValueError(
                "Embed description cannot exceed 4096 characters."
            )

        if embed.footer.text and len(embed.footer.text) > 2048:
            raise ValueError(
                "Embed footer cannot exceed 2048 characters."
            )

    return content, embed


# --------------------------------------------------
# SCHEDULE SERVICE
# --------------------------------------------------

class ScheduleService:
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @tasks.loop(seconds=1)
    async def scheduler_loop(self):
        schedules = await get_active_schedules()

        # Use UTC as the common reference, then convert to each
        # server's configured timezone.
        now_utc = datetime.now(ZoneInfo("UTC"))

        for schedule in schedules:
            schedule_id = schedule.get("_id")

            try:
                guild_id = schedule["guild_id"]

                timezone_name = await get_timezone(guild_id)

                local_now = now_utc.astimezone(
                    ZoneInfo(timezone_name)
                )

                local_date = local_now.date().isoformat()

                # Do not send the same schedule twice on the same date.
                if schedule.get("last_sent_date") == local_date:
                    continue

                # Calculate today's scheduled time in the server timezone.
                scheduled_time = datetime.strptime(
                    schedule["time"],
                    "%H:%M",
                ).time()

                scheduled_at = datetime.combine(
                    local_now.date(),
                    scheduled_time,
                    tzinfo=local_now.tzinfo,
                )

                # Three-second window:
                # 03:51:58 through 03:52:01 for a 03:52 schedule.
                seconds_until = (
                    scheduled_at - local_now
                ).total_seconds()

                if not -1 <= seconds_until <= 2:
                    continue

                # Five-second window:
                # 03:51:56 through 03:52:01 for a 03:52 schedule.
                # seconds_until = (
                #     scheduled_at - local_now
                # ).total_seconds()

                # if not -1 <= seconds_until <= 4:
                #     continue

                # If today's scheduled time has arrived and this schedule
                # has not been sent today, send it.
                # if local_now < scheduled_at:
                #     continue

                guild = self.bot.get_guild(guild_id)

                if guild is None:
                    continue

                channel = guild.get_channel(
                    schedule["channel_id"]
                )

                if not isinstance(channel, discord.TextChannel):
                    continue

                # Always parse the latest message script fetched from
                # MongoDB so edits apply without restarting the bot.
                content, embed = parse_message_script(
                    schedule["message"]
                )

                allowed_mentions = discord.AllowedMentions.none()
                ping_text = None

                # ----------------------------------------------
                # @everyone PING
                # ----------------------------------------------

                if schedule.get("ping_everyone"):
                    ping_text = "@everyone"

                    allowed_mentions = discord.AllowedMentions(
                        everyone=True,
                        users=False,
                        roles=False,
                    )

                # ----------------------------------------------
                # ROLE PING
                # ----------------------------------------------

                elif schedule.get("role_id"):
                    role = guild.get_role(
                        schedule["role_id"]
                    )

                    if role is None:
                        continue

                    ping_text = role.mention

                    allowed_mentions = discord.AllowedMentions(
                        everyone=False,
                        users=False,
                        roles=[role],
                    )

                # ----------------------------------------------
                # USER PING
                # ----------------------------------------------

                elif schedule.get("user_id"):
                    member = guild.get_member(
                        schedule["user_id"]
                    )

                    if member is None:
                        continue

                    ping_text = member.mention

                    allowed_mentions = discord.AllowedMentions(
                        everyone=False,
                        users=[member],
                        roles=False,
                    )

                # Add the ping before the message content.
                if ping_text:
                    content = (
                        f"{ping_text}\n{content}"
                        if content
                        else ping_text
                    )

                # Send the message first, then record successful delivery.
                await channel.send(
                    content=content or None,
                    embed=embed,
                    allowed_mentions=allowed_mentions,
                )

                await mark_schedule_sent(
                    schedule_id,
                    local_date,
                )

                print(
                    f"Sent schedule "
                    f"{schedule.get('name', schedule_id)} "
                    f"in guild {guild_id}."
                )

            except ZoneInfoNotFoundError:
                print(
                    "Invalid timezone for scheduled message "
                    f"in guild {schedule.get('guild_id')}."
                )

            except ValueError as exc:
                print(
                    "Invalid message script or schedule time "
                    f"for schedule {schedule_id}: {exc}"
                )

            except discord.Forbidden:
                print(
                    "Missing permission to send scheduled message "
                    f"{schedule_id}."
                )

            except discord.HTTPException as exc:
                print(
                    f"Failed to send scheduled message "
                    f"{schedule_id}: {exc}"
                )

            except Exception as exc:
                print(
                    f"Scheduled message error for "
                    f"{schedule_id}: {exc}"
                )

    @scheduler_loop.before_loop
    async def before_scheduler_loop(self):
        await self.bot.wait_until_ready()

    def start(self):
        if not self.scheduler_loop.is_running():
            self.scheduler_loop.start()

    def stop(self):
        if self.scheduler_loop.is_running():
            self.scheduler_loop.cancel()
