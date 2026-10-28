import asyncio
import io
from unittest.mock import AsyncMock

import pytest
from PIL import Image

from .helpers import image_bytes


@pytest.mark.parametrize(
    "source_format,target,extension",
    [
        ("png", "PNG", "png"),
        ("gif", "GIF", "gif"),
        ("apng", "APNG", "png"),
        ("lottie", "JSON", "json"),
    ],
)
def test_sticker_exports_keep_the_original_file_when_formats_match(
    import_app, monkeypatch, source_format, target, extension
):
    module = import_app("app.cogs.steal_emoji")
    data = (
        b'{"layers":[]}'
        if source_format == "lottie"
        else image_bytes(
            "GIF" if source_format == "gif" else "PNG",
            animated=source_format in {"gif", "apng"},
        )
    )
    monkeypatch.setattr(module.export, "download_file", AsyncMock(return_value=data))
    item = module.StealItem(
        "sticker", "wave", 123, "https://example.com/sticker", source_format != "png", source_format
    )
    assert asyncio.run(module.export_item(item, target)) == (data, extension)


def test_converting_animated_stickers_to_gif_preserves_earlier_frame_colors(
    import_app, monkeypatch
):
    module = import_app("app.cogs.steal_emoji")
    data = image_bytes(animated=True)
    monkeypatch.setattr(module.export, "download_file", AsyncMock(return_value=data))
    item = module.StealItem("sticker", "wave", 123, "https://example.com/sticker.png", True, "apng")
    result, extension = asyncio.run(module.export_item(item, "GIF"))
    assert extension == "gif"
    with Image.open(io.BytesIO(result)) as image:
        assert image.n_frames == 2 and image.info["duration"] == 100
        first = image.convert("RGBA").getpixel((160, 160))
        image.seek(1)
        second = image.convert("RGBA").getpixel((160, 160))
        assert first[0] > 200 and first[2] < 50
        assert second[2] > 200 and second[0] < 50
