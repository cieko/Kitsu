"""Emoji gif public interface."""

from .cog import EmojiGif as EmojiGif
from .cog import setup as setup
from .constants import CUSTOM_EMOJI_PATTERN as CUSTOM_EMOJI_PATTERN
from .constants import DEFAULT_GIF_DOMAINS as DEFAULT_GIF_DOMAINS
from .detection import contains_custom_emoji as contains_custom_emoji
from .detection import has_link as has_link
from .detection import is_allowed_custom_message as is_allowed_custom_message
from .detection import is_allowed_emoji_gif_message as is_allowed_emoji_gif_message
from .detection import is_custom_emoji_only as is_custom_emoji_only
from .detection import is_gif_attachment as is_gif_attachment
from .detection import is_gif_embed as is_gif_embed
from .detection import is_gif_url as is_gif_url
from .detection import is_image_attachment as is_image_attachment
from .detection import is_video_attachment as is_video_attachment

__all__ = [
    "CUSTOM_EMOJI_PATTERN",
    "DEFAULT_GIF_DOMAINS",
    "EmojiGif",
    "contains_custom_emoji",
    "has_link",
    "is_allowed_custom_message",
    "is_allowed_emoji_gif_message",
    "is_custom_emoji_only",
    "is_gif_attachment",
    "is_gif_embed",
    "is_gif_url",
    "is_image_attachment",
    "is_video_attachment",
    "setup",
]
