from __future__ import annotations

import asyncio
import io
import logging
import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import parse_qs, urlparse

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

logger = logging.getLogger(__name__)

STEAL_TIMEOUT = 300
ASSET_COLOR = discord.Colour(0x7DBBEB)
CDN_EMOJI_URL = "https://cdn.discordapp.com/emojis"

CUSTOM_EMOJI_PATTERN = re.compile(
    r"<(?P<animated>a?):(?P<name>[A-Za-z0-9_]{2,32}):(?P<id>\d{15,25})>"
)

FORWARDED_EMOJI_PATTERN = re.compile(
    r"\[:(?P<name>[A-Za-z0-9_]{2,32}):\]"
    r"\(\s*<?(?P<url>https?://cdn\.discordapp\.com/emojis/"
    r"(?P<id>\d{15,25})\.(?P<ext>png|gif|webp|jpeg|jpg)"
    r"(?:\?[^)\s>]*)?)>?\s*\)",
    re.IGNORECASE,
)

RAW_EMOJI_URL_PATTERN = re.compile(
    r"https?://cdn\.discordapp\.com/emojis/"
    r"(?P<id>\d{15,25})\.(?P<ext>png|gif|webp|jpeg|jpg)"
    r"(?:\?[^\s<>]*)?",
    re.IGNORECASE,
)


@dataclass(slots=True)
class StealItem:
    item_type: Literal["emoji", "sticker"]
    name: str
    item_id: int
    url: str
    animated: bool
    source_format: str

    @property
    def export_options(self) -> list[str]:
        if self.source_format == "lottie":
            return ["JSON"]

        if self.animated:
            return ["GIF", "APNG"]

        if self.source_format == "unknown":
            return ["PNG"]

        return ["PNG", "GIF", "WEBP", "JPEG"]


def normalize_url(url: str) -> str:
    return url.replace(r"\&", "&").replace("&amp;", "&")


def _emoji_item(
    *,
    name: str,
    emoji_id: int,
    url: str,
    extension: str,
    animated: bool | None = None,
) -> StealItem:
    url = normalize_url(url)
    query = parse_qs(urlparse(url).query)

    is_animated = animated if animated is not None else (
        extension.lower() == "gif"
        or query.get("animated", ["false"])[0].lower() == "true"
    )

    if is_animated:
        url = (
            f"{CDN_EMOJI_URL}/{emoji_id}.gif"
            "?size=4096&quality=lossless"
        )
        extension = "gif"

    return StealItem(
        item_type="emoji",
        name=name,
        item_id=emoji_id,
        url=url,
        animated=is_animated,
        source_format=extension.lower(),
    )


def get_custom_emojis(content: str) -> list[StealItem]:
    items: list[StealItem] = []
    seen: set[int] = set()

    def add(item: StealItem) -> None:
        if item.item_id in seen:
            return

        seen.add(item.item_id)
        items.append(item)

        logger.info(
            "[STEAL] Emoji found | name=%s | id=%s | animated=%s | format=%s",
            item.name,
            item.item_id,
            item.animated,
            item.source_format,
        )

    for match in CUSTOM_EMOJI_PATTERN.finditer(content):
        emoji_id = int(match.group("id"))
        animated = bool(match.group("animated"))
        extension = "gif" if animated else "png"

        add(
            _emoji_item(
                name=match.group("name"),
                emoji_id=emoji_id,
                url=(
                    f"{CDN_EMOJI_URL}/{emoji_id}.{extension}"
                    "?size=4096&quality=lossless"
                ),
                extension=extension,
                animated=animated,
            )
        )

    for match in FORWARDED_EMOJI_PATTERN.finditer(content):
        add(
            _emoji_item(
                name=match.group("name"),
                emoji_id=int(match.group("id")),
                url=match.group("url"),
                extension=match.group("ext"),
            )
        )

    for match in RAW_EMOJI_URL_PATTERN.finditer(content):
        emoji_id = int(match.group("id"))

        if emoji_id in seen:
            continue

        add(
            _emoji_item(
                name=f"emoji_{emoji_id}",
                emoji_id=emoji_id,
                url=match.group(0),
                extension=match.group("ext"),
            )
        )

    logger.info(
        "[STEAL] Emoji extraction finished | total=%d",
        len(items),
    )

    return items


def get_message_stickers(
    message: discord.Message,
) -> list[StealItem]:
    stickers: list[StealItem] = []

    format_map = {
        "png": "png",
        "apng": "apng",
        "lottie": "lottie",
        "gif": "gif",
    }

    for sticker in message.stickers:
        sticker_format = getattr(sticker.format, "name", "").lower()
        source_format = format_map.get(sticker_format, "unknown")

        item = StealItem(
            item_type="sticker",
            name=sticker.name,
            item_id=sticker.id,
            url=str(sticker.url),
            animated=source_format in {"apng", "gif", "lottie"},
            source_format=source_format,
        )

        stickers.append(item)

        logger.info(
            "[STEAL] Sticker found | name=%s | id=%s | format=%s",
            item.name,
            item.item_id,
            item.source_format,
        )

    return stickers


def _embed_text(embed: discord.Embed) -> list[str]:
    parts = [
        embed.title or "",
        embed.description or "",
        embed.url or "",
    ]

    for field in embed.fields:
        parts.extend((field.name or "", field.value or ""))

    return parts


def get_message_items(message: discord.Message) -> list[StealItem]:
    logger.info(
        "[STEAL] Inspecting message | id=%s | author=%s | channel=%s "
        "| embeds=%d | stickers=%d",
        message.id,
        message.author,
        message.channel.id,
        len(message.embeds),
        len(message.stickers),
    )

    content_parts = [message.content or ""]

    for snapshot in getattr(message, "message_snapshots", None) or []:
        content_parts.append(getattr(snapshot, "content", None) or "")

        for embed in getattr(snapshot, "embeds", None) or []:
            content_parts.extend(_embed_text(embed))

    for embed in message.embeds:
        content_parts.extend(_embed_text(embed))

    emojis = get_custom_emojis("\n".join(content_parts))
    stickers = get_message_stickers(message)

    result: list[StealItem] = []
    seen: set[tuple[str, int]] = set()

    for item in emojis + stickers:
        key = (item.item_type, item.item_id)

        if key in seen:
            continue

        seen.add(key)
        result.append(item)

    logger.info(
        "[STEAL] Final extraction | emojis=%d | stickers=%d | total=%d",
        len(emojis),
        len(stickers),
        len(result),
    )

    return result


async def download_file(url: str) -> bytes:
    timeout = aiohttp.ClientTimeout(total=30)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url) as response:
            logger.info(
                "[STEAL] CDN response | status=%s | content_type=%s",
                response.status,
                response.headers.get("Content-Type"),
            )

            if response.status != 200:
                raise RuntimeError(
                    f"Discord CDN returned HTTP {response.status}."
                )

            return await response.read()


def convert_image(data: bytes, target_format: str) -> bytes:
    try:
        from PIL import Image, ImageSequence
    except ImportError as exc:
        raise RuntimeError(
            "Image conversion requires Pillow. "
            "Run: python -m pip install Pillow"
        ) from exc

    output = io.BytesIO()

    with Image.open(io.BytesIO(data)) as image:
        loop = image.info.get("loop", 0)

        frames = [
            frame.convert("RGBA")
            for frame in ImageSequence.Iterator(image)
        ]

        durations = [
            frame.info.get("duration", 100) or 100
            for frame in ImageSequence.Iterator(image)
        ]

        first = frames[0]

        if target_format == "PNG":
            first.save(output, format="PNG")

        elif target_format == "GIF":
            palette_source = Image.new(
                "RGBA",
                first.size,
                (0, 0, 0, 0),
            )

            for frame in frames:
                palette_source.alpha_composite(frame)

            rgb_source = Image.new("RGB", palette_source.size, "white")
            rgb_source.paste(
                palette_source.convert("RGB"),
                mask=palette_source.getchannel("A"),
            )

            palette = rgb_source.quantize(
                colors=255,
                method=Image.Quantize.MEDIANCUT,
            )

            palette_data = (palette.getpalette() + [0] * 768)[:768]
            palette_data[765:768] = [0, 0, 0]

            gif_frames = []

            for frame in frames:
                alpha = frame.getchannel("A")
                rgb = Image.new("RGB", frame.size, "white")
                rgb.paste(frame.convert("RGB"), mask=alpha)

                indexed = rgb.quantize(
                    palette=palette,
                    dither=Image.Dither.FLOYDSTEINBERG,
                )

                indexed.putpalette(palette_data)

                pixels = indexed.load()
                alpha_pixels = alpha.load()

                for y in range(frame.height):
                    for x in range(frame.width):
                        if alpha_pixels[x, y] < 128:
                            pixels[x, y] = 255

                gif_frames.append(indexed)

            gif_frames[0].save(
                output,
                format="GIF",
                save_all=len(gif_frames) > 1,
                append_images=gif_frames[1:],
                duration=durations if len(gif_frames) > 1 else 100,
                loop=loop,
                optimize=False,
                transparency=255,
                disposal=2,
            )

        elif target_format == "APNG":
            first.save(
                output,
                format="PNG",
                save_all=True,
                append_images=frames[1:],
                duration=durations if len(frames) > 1 else None,
                loop=loop,
                default_image=False,
            )

        elif target_format == "WEBP":
            first.save(
                output,
                format="WEBP",
                save_all=len(frames) > 1,
                append_images=frames[1:],
                duration=durations if len(frames) > 1 else None,
                loop=loop,
            )

        elif target_format == "JPEG":
            background = Image.new("RGB", first.size, "white")
            background.paste(first, mask=first.getchannel("A"))
            background.save(output, format="JPEG", quality=95)

        else:
            raise RuntimeError(f"Unsupported format: {target_format}")

    return output.getvalue()


async def export_item(
    item: StealItem,
    target_format: str,
) -> tuple[bytes, str]:
    data = await download_file(item.url)

    if target_format == "JSON":
        if item.source_format != "lottie":
            raise RuntimeError(
                "JSON export is only supported for Lottie stickers."
            )

        return data, "json"

    if target_format == "GIF" and item.source_format == "gif":
        return data, "gif"

    if target_format == "APNG" and item.source_format == "apng":
        return data, "png"

    if target_format == "PNG" and item.source_format == "png":
        return data, "png"

    converted = await asyncio.to_thread(
        convert_image,
        data,
        target_format,
    )

    extension = {
        "PNG": "png",
        "GIF": "gif",
        "APNG": "png",
        "WEBP": "webp",
        "JPEG": "jpg",
    }[target_format]

    return converted, extension


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

        bot_member = guild.me

        if (
            bot_member is None
            or not bot_member.guild_permissions.manage_emojis_and_stickers
        ):
            await interaction.response.send_message(
                "I need the **Manage Emojis and Stickers** permission.",
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
                f"Successfully added {emoji} (`{emoji.name}`) "
                f"to **{guild.name}**!",
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
        parts.append(
            discord.ui.TextDisplay(
                f"[Open sticker preview]({item.url})"
            )
        )
    else:
        parts.append(
            discord.ui.MediaGallery(
                discord.MediaGalleryItem(item.url)
            )
        )

    parts.extend(
        [
            discord.ui.Separator(),
            discord.ui.TextDisplay(
                f"-# Page **{page + 1}/{total}**"
            ),
        ]
    )

    return discord.ui.Container(
        *parts,
        accent_colour=ASSET_COLOR,
    )


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

        self.message: (
            discord.Message | discord.InteractionMessage | None
        ) = None

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

        container.add_item(
            discord.ui.ActionRow(export_select)
        )

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

        container.add_item(
            discord.ui.ActionRow(previous, next_button)
        )

        # Main asset preview: shown for both slash and prefix commands.
        self.add_item(container)

        # Privacy note + Make Public: shown only for slash commands.
        if self.is_slash:
            self.add_item(
                discord.ui.Container(
                    discord.ui.Section(
                        discord.ui.TextDisplay(
                            "*_Note: Only you can view this emoji preview_*"
                        ),
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

        self.expiry_task = asyncio.create_task(
            self._expire_after_timeout()
        )

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

        self.add_item(
            discord.ui.TextDisplay(
                "-# The message is now inactive."
            )
        )

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

        container.add_item(
            discord.ui.ActionRow(export_select)
        )

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
                        discord.ui.TextDisplay(
                            "*`_Note: Only you can view this emoji preview_`*"
                        ),
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
                "I couldn't publish the preview. "
                "Check the bot's channel permissions.",
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
                content=(
                    f"Exported **{item.name}** "
                    f"as **{selected_format}**."
                ),
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


class StealEmoji(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def show_results(
        self,
        *,
        items: list[StealItem],
        owner_id: int,
        ctx: commands.Context | None = None,
        interaction: discord.Interaction | None = None,
    ) -> None:
        if not items:
            message = (
                "I couldn't find any custom emojis or stickers "
                "in that message."
            )

            if interaction is not None:
                await interaction.response.send_message(
                    message,
                    ephemeral=True,
                )
            elif ctx is not None:
                await ctx.reply(
                    message,
                    mention_author=False,
                )

            return

        # This flag determines whether the second container appears.
        view = StealEmojiView(
            items=items,
            owner_id=owner_id,
            is_slash=interaction is not None,
        )

        if interaction is not None:
            await interaction.response.send_message(
                view=view,
                ephemeral=True,
            )
            view.message = await interaction.original_response()

        elif ctx is not None:
            # Prefix-command previews are public.
            view.message = await ctx.reply(
                view=view,
                mention_author=False,
            )

        view.start_expiry_timer()

    @commands.command(name="steal")
    async def steal_prefix(
        self,
        ctx: commands.Context,
    ) -> None:
        reference = ctx.message.reference

        logger.info(
            "[STEAL] Prefix command | user=%s | message_id=%s",
            ctx.author,
            ctx.message.id,
        )

        if reference is None:
            await ctx.reply(
                "Reply to a message containing emojis or stickers, "
                "then run the steal command.",
                mention_author=False,
            )
            return

        message = reference.resolved

        if not isinstance(message, discord.Message):
            if reference.message_id is None:
                await ctx.reply(
                    "I couldn't resolve the referenced message.",
                    mention_author=False,
                )
                return

            try:
                message = await ctx.channel.fetch_message(
                    reference.message_id
                )
            except discord.HTTPException:
                logger.exception(
                    "[STEAL] Failed to fetch replied-to message"
                )
                await ctx.reply(
                    "I couldn't retrieve the referenced message.",
                    mention_author=False,
                )
                return

        await self.show_results(
            items=get_message_items(message),
            owner_id=ctx.author.id,
            ctx=ctx,
        )

    @app_commands.command(
        name="steal",
        description="Extract custom emojis and stickers from a message.",
    )
    @app_commands.describe(
        message_id="ID of a message in this channel"
    )
    @app_commands.guild_only()
    async def steal_slash(
        self,
        interaction: discord.Interaction,
        message_id: str,
    ) -> None:
        channel = interaction.channel

        logger.info(
            "[STEAL] Slash command | user=%s | channel=%s | message_id=%r",
            interaction.user,
            getattr(channel, "id", None),
            message_id,
        )

        if channel is None or not hasattr(channel, "fetch_message"):
            await interaction.response.send_message(
                "I can't fetch messages from this channel.",
                ephemeral=True,
            )
            return

        try:
            target_id = int(message_id)

            if target_id <= 0:
                raise ValueError

        except ValueError:
            await interaction.response.send_message(
                "Please provide a valid message ID.",
                ephemeral=True,
            )
            return

        try:
            message = await channel.fetch_message(target_id)
        except discord.HTTPException:
            logger.exception(
                "[STEAL] Failed to fetch slash-command target"
            )
            await interaction.response.send_message(
                "I couldn't retrieve that message from this channel.",
                ephemeral=True,
            )
            return

        await self.show_results(
            items=get_message_items(message),
            owner_id=interaction.user.id,
            interaction=interaction,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(StealEmoji(bot))

