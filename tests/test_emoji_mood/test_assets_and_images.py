import io
import json
from types import SimpleNamespace

from PIL import Image
import pytest

from app.cogs.emoji_mood.assets import message_assets
from app.cogs.emoji_mood.presentation import result_embed
from app.services.emoji_mood import MoodAsset, MoodError, MoodResult
from app.services.emoji_mood.images import prepare_image, validate_asset

from .helpers import asset, image_bytes, message, unicode_asset


def test_extracts_deduplicated_custom_unicode_and_forwarded_stickers():
    sticker = SimpleNamespace(id=333, name="wave", format=SimpleNamespace(name="apng"),
                              url="https://cdn.discordapp.com/stickers/333.png")
    current = message("<a:dancing:123456789012345> 😀😀", message_snapshots=[
        SimpleNamespace(content="😍", stickers=[sticker], embeds=[]),
    ])
    assets = message_assets(current)
    assert [(item.kind, item.item_id, item.unicode) for item in assets] == [
        ("emoji", 123456789012345, ""), ("sticker", 333, ""),
        ("unicode", 0, "😀"), ("unicode", 0, "😍"),
    ]
    assert assets[0].url.endswith(".gif?size=128&quality=lossless")
    assert message_assets(message("An ordinary message.")) == []


@pytest.mark.parametrize("url", [
    "http://cdn.discordapp.com/emojis/123456789012345.png",
    "https://cdn.discordapp.com.evil.example/emojis/123456789012345.png",
    "https://localhost/emojis/123456789012345.png",
    "https://cdn.discordapp.com/attachments/123456789012345.png",
    "https://cdn.discordapp.com/emojis/123456789012346.png",
    "https://name:password@cdn.discordapp.com/emojis/123456789012345.png",
    "https://cdn.discordapp.com:1234/emojis/123456789012345.png",
    "https://cdn.discordapp.com/emojis/123456789012345.json",
])
def test_only_the_expected_discord_image_can_be_downloaded(url):
    with pytest.raises(MoodError):
        validate_asset(MoodAsset("emoji", "test", 123456789012345, url, "png"))


def test_lottie_is_reported_as_unsupported_without_guessing_a_mood():
    with pytest.raises(MoodError, match="Lottie"):
        validate_asset(MoodAsset("sticker", "test", 123, "https://cdn.discordapp.com/stickers/123.json", "lottie"))


def test_static_and_animated_images_preserve_visible_frames():
    static, samples, total = prepare_image(image_bytes())
    assert samples == total == 1
    with Image.open(io.BytesIO(static)) as image:
        assert image.size == (256, 256)
        assert image.getpixel((128, 128)) == (0, 0, 255)
    animated, samples, total = prepare_image(image_bytes(frames=8))
    assert samples == 6 and total == 8
    with Image.open(io.BytesIO(animated)) as image:
        assert image.size == (768, 552)
        assert image.getpixel((128, 128)) == (0, 0, 255)
        assert image.getpixel((640, 404)) == (140, 0, 115)


def test_image_limits_and_invalid_data_are_reported(monkeypatch):
    from app.services.emoji_mood import images

    with pytest.raises(MoodError, match="read"):
        prepare_image(b"not an image")
    monkeypatch.setattr(images, "MAX_PIXELS", 10)
    with pytest.raises(MoodError, match="too large"):
        prepare_image(image_bytes())
    monkeypatch.setattr(images, "MAX_PIXELS", 4096 * 4096)
    monkeypatch.setattr(images, "MAX_ANIMATION_FRAMES", 1)
    with pytest.raises(MoodError, match="too many frames"):
        prepare_image(image_bytes(frames=2))


@pytest.mark.parametrize("value", [
    None, [], {}, {"moods": [], "nsfw": "unlikely", "nudity": "unlikely", "reason": "none"},
    {"moods": ["happy"], "nsfw": "safe", "nudity": "unlikely", "reason": "none"},
    {"moods": ["made_up"], "nsfw": "unlikely", "nudity": "unlikely", "reason": "none"},
    {"moods": ["happy"], "nsfw": "unlikely", "nudity": "unlikely", "reason": ""},
    {"moods": ["happy"], "nsfw": "unlikely", "nudity": "unlikely", "reason": "x" * 241},
    {"moods": ["happy"], "nsfw": "unlikely", "nudity": "unlikely", "reason": "ok", "extra": True},
])
def test_invalid_model_output_never_becomes_a_successful_classification(value):
    with pytest.raises(MoodError):
        MoodResult.parse(json.dumps(value))


def test_nsfw_and_mood_are_independent_and_rendering_does_not_ping():
    result = MoodResult.parse(json.dumps({"moods": ["happy", "happy", "neutral"], "nsfw": "likely", "nudity": "unlikely",
                                         "reason": "@everyone **untrusted**"}))
    assert result.moods == ("happy",)
    embed = result_embed(asset(), result)
    assert "NSFW" in embed.fields[1].value
    assert embed.fields[2].value == "Likely"
    assert "@everyone" not in embed.fields[4].value
    assert embed.thumbnail.url is None
    assert "Unicode" in result_embed(unicode_asset(), result).footer.text
