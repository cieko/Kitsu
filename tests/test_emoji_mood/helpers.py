import io
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from PIL import Image

from app.services.emoji_mood import MoodAsset, MoodResult


def image_bytes(*, frames=1):
    images = [Image.new("RGBA", (32, 32), (index * 20, 0, 255 - index * 20, 255)) for index in range(frames)]
    output = io.BytesIO()
    images[0].save(output, format="PNG", save_all=frames > 1,
                   append_images=images[1:], duration=100, loop=0)
    return output.getvalue()


def asset(item_id=123456789012345, **kwargs):
    return MoodAsset("emoji", "untrusted_asset_name", item_id,
                     f"https://cdn.discordapp.com/emojis/{item_id}.png", "png", **kwargs)


def unicode_asset(symbol="😀"):
    return MoodAsset("unicode", "grinning_face", unicode=symbol)


def result():
    return MoodResult(("happy", "fun"), "unlikely", "A smiling face.", 1, 1, nudity="unlikely")


def payload(**changes):
    value = {"moods": ["happy", "fun"], "nsfw": "unlikely", "nudity": "unlikely", "reason": "A smiling face."}
    value.update(changes)
    return {"done": True, "done_reason": "stop", "message": {"content": json.dumps(value)}}


def message(content="😀", **changes):
    values = dict(id=99, content=content, guild=SimpleNamespace(id=1),
                  author=SimpleNamespace(id=7, bot=False), channel=SimpleNamespace(id=3),
                  stickers=[], embeds=[], message_snapshots=[], webhook_id=None,
                  reply=AsyncMock(), delete=AsyncMock())
    values.update(changes)
    return SimpleNamespace(**values)


def classifier():
    return SimpleNamespace(classify=AsyncMock(return_value=result()))
