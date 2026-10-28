from __future__ import annotations

import asyncio
import io

import discord
from PIL import Image, ImageSequence

from .export import convert_image
from .models import StealItem

EMOJI_UPLOAD_LIMIT = 256 * 1024
STICKER_UPLOAD_LIMIT = 512 * 1024


def prepare_image(data: bytes, *, sticker: bool) -> tuple[bytes, str]:
    """Prepare server-upload bytes independently of the enlarged preview URL."""
    size = 320 if sticker else 128
    limit = STICKER_UPLOAD_LIMIT if sticker else EMOJI_UPLOAD_LIMIT
    label = "sticker" if sticker else "emoji"
    try:
        with Image.open(io.BytesIO(data)) as image:
            animated = bool(getattr(image, "is_animated", False))
            frames = []
            durations = []
            loop = image.info.get("loop", 0)
            # APNG files may carry a static poster before their animation.
            first_frame = int(bool(image.info.get("default_image")))
            for index, frame in enumerate(ImageSequence.Iterator(image)):
                if index < first_frame:
                    continue
                durations.append(frame.info.get("duration", 100) or 100)
                scaled = frame.convert("RGBA")
                scaled.thumbnail((size, size), Image.Resampling.LANCZOS)
                canvas = Image.new("RGBA", (size, size))
                canvas.alpha_composite(
                    scaled, ((size - scaled.width) // 2, (size - scaled.height) // 2)
                )
                frames.append(canvas)

            if sticker and animated and sum(durations) > 5000:
                raise ValueError("Animated stickers must be at most 5 seconds long.")
            valid_size = image.size == (320, 320) if sticker else max(image.size) <= 128
            if (
                valid_size
                and len(data) <= limit
                and image.format
                in ({"PNG", "GIF"} if sticker else {"PNG", "GIF", "JPEG", "WEBP", "AVIF"})
                and (sticker or not animated or image.format != "PNG")
            ):
                extension = {"PNG": "png", "JPEG": "jpg"}.get(image.format, image.format.lower())
                return data, extension

            output = io.BytesIO()
            save_options = {}
            if animated and len(frames) > 1:
                save_options = {
                    "save_all": True,
                    "append_images": frames[1:],
                    "duration": durations,
                    "loop": loop,
                }
            frames[0].save(output, format="PNG", **save_options)
            prepared = output.getvalue()
            extension = "png"
            if animated and not sticker:
                prepared = convert_image(prepared, "GIF")
                extension = "gif"
    except (OSError, EOFError) as error:
        raise ValueError(f"Discord returned an unreadable {label} image.") from error

    if len(prepared) > limit:
        raise ValueError(
            f"This {label} is still larger than {limit // 1024} KiB after resizing. "
            "Export a smaller version before adding it."
        )
    return prepared, extension


async def add_to_server(
    guild: discord.Guild, item: StealItem, data: bytes, *, actor
) -> discord.Emoji | discord.GuildSticker:
    """Upload the selected asset to its matching guild-expression endpoint."""
    if item.item_type == "emoji":
        image, _ = await asyncio.to_thread(prepare_image, data, sticker=False)
        return await guild.create_custom_emoji(
            name=item.name[:32],
            image=image,
            reason=f"Emoji added by {actor}",
        )

    if item.source_format == "lottie":
        if not {"VERIFIED", "PARTNERED"}.intersection(guild.features):
            raise ValueError(
                "Discord only allows Lottie sticker uploads in verified or partnered servers."
            )
        if len(data) > STICKER_UPLOAD_LIMIT:
            raise ValueError("Sticker files cannot exceed 512 KiB.")
        image, extension = data, "json"
    else:
        image, extension = await asyncio.to_thread(prepare_image, data, sticker=True)

    name = item.name.strip()[:30]
    if len(name) < 2:
        name = f"sticker_{item.item_id}"[:30]
    description = item.description.strip()[:100]
    if len(description) == 1:
        description = ""
    with io.BytesIO(image) as buffer:
        file = discord.File(buffer, filename=f"sticker_{item.item_id}.{extension}")
        try:
            return await guild.create_sticker(
                name=name,
                description=description,
                emoji=(item.emoji or "🍥")[:200],
                file=file,
                reason=f"Sticker added by {actor}",
            )
        finally:
            file.close()
