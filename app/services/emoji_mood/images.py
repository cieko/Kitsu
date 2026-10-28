from __future__ import annotations

import io
import math
from urllib.parse import urlsplit

from PIL import Image, ImageDraw, UnidentifiedImageError

from .models import MoodAsset, MoodError


MAX_DOWNLOAD_BYTES = 8 * 1024 * 1024
MAX_PIXELS = 4096 * 4096
MAX_ANIMATION_FRAMES = 500
MAX_SAMPLES = 6
TILE_SIZE = 256


def validate_asset(asset: MoodAsset) -> None:
    """Restrict downloads to Discord assets, never arbitrary message URLs."""
    if asset.kind not in {"emoji", "sticker"} or asset.item_id <= 0:
        raise MoodError("I couldn't read that emoji or sticker.")
    if asset.source_format == "lottie":
        raise MoodError("I can't classify Lottie stickers yet. Try a PNG, APNG, or GIF sticker.")
    try:
        parsed = urlsplit(asset.url)
        folder = "emojis" if asset.kind == "emoji" else "stickers"
        stem, extension = parsed.path.rsplit(".", 1)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in {"cdn.discordapp.com", "media.discordapp.net"}
            or parsed.username is not None or parsed.password is not None
            or parsed.port not in {None, 443}
            or stem != f"/{folder}/{asset.item_id}"
            or extension.lower() not in {"png", "gif", "webp", "jpg", "jpeg", "avif"}
        ):
            raise ValueError
    except ValueError as error:
        raise MoodError("I can only inspect emoji and sticker images hosted by Discord.") from error


def prepare_image(data: bytes) -> tuple[bytes, int, int]:
    """Build one small contact sheet; samples include the first and last frames."""
    if not data or len(data) > MAX_DOWNLOAD_BYTES:
        raise MoodError("That emoji or sticker file is too large to inspect.")
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.width * image.height > MAX_PIXELS:
                raise MoodError("That emoji or sticker image is too large to inspect.")
            total = getattr(image, "n_frames", 1)
            first = int(bool(image.info.get("default_image")))
            available = total - first
            if available <= 0 or total > MAX_ANIMATION_FRAMES:
                raise MoodError("That animation has too many frames to inspect safely.")
            count = min(MAX_SAMPLES, available)
            indices = [first] if count == 1 else [
                first + round(index * (available - 1) / (count - 1))
                for index in range(count)
            ]
            columns = min(3, count)
            label_height = 20 if count > 1 else 0
            sheet = Image.new(
                "RGB", (columns * TILE_SIZE, math.ceil(count / columns) * (TILE_SIZE + label_height)),
                "#ededed",
            )
            draw = ImageDraw.Draw(sheet)
            for position, index in enumerate(indices):
                image.seek(index)
                frame = image.convert("RGBA")
                frame.thumbnail((TILE_SIZE, TILE_SIZE), Image.Resampling.LANCZOS)
                x, y = position % columns * TILE_SIZE, position // columns * (TILE_SIZE + label_height)
                sheet.paste(frame, (x + (TILE_SIZE - frame.width) // 2, y + (TILE_SIZE - frame.height) // 2), frame)
                if label_height:
                    draw.text((x + 5, y + TILE_SIZE + 2), f"Frame {index + 1}", fill="black")
            output = io.BytesIO()
            sheet.save(output, format="PNG")
            return output.getvalue(), count, available
    except (UnidentifiedImageError, OSError, EOFError, ValueError, Image.DecompressionBombError) as error:
        raise MoodError("I couldn't read that emoji or sticker image.") from error
