from __future__ import annotations

CONTENT_TYPES = {
    "text": "Text",
    "custom_emoji": "Emoji",
    "gif": "GIF",
    "images": "Images",
    "videos": "Videos",
    "links": "Links",
    "stickers": "Stickers",
}


DEFAULT_RULES = {
    "text": False,
    "custom_emoji": False,
    "gif": False,
    "images": False,
    "videos": False,
    "links": False,
    "stickers": False,
}


DEFAULT_GIF_DOMAINS = [
    "tenor.com",
    "media.tenor.com",
    "giphy.com",
    "media.giphy.com",
    "i.giphy.com",
]
