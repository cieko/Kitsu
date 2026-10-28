from __future__ import annotations

import re

import discord

from .constants import CUSTOM_EMOJI_PATTERN, DEFAULT_GIF_DOMAINS


def contains_custom_emoji(content: str) -> bool:
    return bool(CUSTOM_EMOJI_PATTERN.search(content))


def is_custom_emoji_only(content: str) -> bool:
    cleaned = CUSTOM_EMOJI_PATTERN.sub(
        "",
        content,
    ).strip()

    return bool(CUSTOM_EMOJI_PATTERN.search(content)) and not cleaned


def is_gif_url(content: str) -> bool:
    urls = re.findall(
        r"https?://[^\s]+",
        content.lower(),
    )

    if not urls:
        return False

    return all(url.endswith(".gif") or ".gif?" in url for url in urls)


def is_gif_attachment(
    message: discord.Message,
) -> bool:

    for attachment in message.attachments:
        content_type = (attachment.content_type or "").lower()

        if content_type == "image/gif":
            return True

        if attachment.filename.lower().endswith(".gif"):
            return True

    return False


def is_image_attachment(
    message: discord.Message,
) -> bool:

    for attachment in message.attachments:
        content_type = (attachment.content_type or "").lower()

        if content_type.startswith("image/"):
            return True

        if attachment.filename.lower().endswith(
            (
                ".png",
                ".jpg",
                ".jpeg",
                ".webp",
                ".bmp",
            )
        ):
            return True

    return False


def is_video_attachment(
    message: discord.Message,
) -> bool:

    for attachment in message.attachments:
        content_type = (attachment.content_type or "").lower()

        if content_type.startswith("video/"):
            return True

        if attachment.filename.lower().endswith(
            (
                ".mp4",
                ".mov",
                ".webm",
                ".mkv",
                ".avi",
            )
        ):
            return True

    return False


def has_link(content: str) -> bool:
    return bool(
        re.search(
            r"https?://[^\s]+",
            content,
            re.IGNORECASE,
        )
    )


def is_gif_embed(
    message: discord.Message,
    gif_domains: list[str] | tuple[str, ...],
) -> bool:

    for embed in message.embeds:
        # Discord identified this embed as a GIF.
        if embed.type == "gifv":
            return True

        embed_url = (embed.url or "").lower()

        # "*" means GIFs from any domain are allowed.
        if "*" in gif_domains:
            return True

        if any(domain in embed_url for domain in gif_domains):
            return True

    return False


def is_allowed_custom_message(
    message: discord.Message,
    rules: dict,
    gif_domains: list[str] | tuple[str, ...],
) -> bool:

    content = message.content.strip()

    # --------------------------------------------------------
    # Stickers
    # --------------------------------------------------------

    if message.stickers:
        return rules.get(
            "stickers",
            False,
        )

    # --------------------------------------------------------
    # GIF attachments / embeds
    # --------------------------------------------------------

    if is_gif_attachment(message) or is_gif_embed(
        message,
        gif_domains,
    ):
        return rules.get(
            "gif",
            False,
        )

    # --------------------------------------------------------
    # Other attachments
    # --------------------------------------------------------

    if message.attachments:
        # GIF was already checked above.

        if is_image_attachment(message):
            return rules.get(
                "images",
                False,
            )

        if is_video_attachment(message):
            return rules.get(
                "videos",
                False,
            )

        # Generic files are not configurable,
        # so reject them.
        return False

    # --------------------------------------------------------
    # Links / GIF URLs
    # --------------------------------------------------------

    if content and has_link(content):
        urls = re.findall(
            r"https?://[^\s]+",
            content.lower(),
        )

        # GIF URL
        if is_gif_url(content):
            if not rules.get(
                "gif",
                False,
            ):
                return False

            if "*" in gif_domains:
                return True

            return all(any(domain in url for domain in gif_domains) for url in urls)

        # Normal link
        return rules.get(
            "links",
            False,
        )

    # --------------------------------------------------------
    # Discord custom emoji
    # --------------------------------------------------------

    if is_custom_emoji_only(content):
        return rules.get(
            "custom_emoji",
            False,
        )

    # --------------------------------------------------------
    # Text
    #
    # Unicode emoji are considered text.
    #
    # Example:
    #   hello
    #   hello 😂
    #   😂
    #
    # All of these are controlled by "text".
    # --------------------------------------------------------

    if content:
        return rules.get(
            "text",
            False,
        )

    # --------------------------------------------------------
    # Empty / unknown content
    # --------------------------------------------------------

    return False


def is_allowed_emoji_gif_message(
    message: discord.Message,
) -> bool:

    content = message.content.strip()

    # Stickers
    if message.stickers:
        return True

    # GIF attachment
    if is_gif_attachment(message):
        return True

    # GIF embed
    if is_gif_embed(
        message,
        DEFAULT_GIF_DOMAINS,
    ):
        return True

    # GIF URL
    if content and is_gif_url(content):
        return True

    # Discord custom emoji
    if content and is_custom_emoji_only(content):
        return True

    # Unicode emoji
    #
    # Unicode emoji are allowed by the preset.
    #
    # We intentionally don't need a separate
    # "unicode_emoji" rule anymore.
    if content:
        import emoji

        if (
            emoji.emoji_count(content) > 0
            and not emoji.replace_emoji(
                content,
                replace="",
            ).strip()
        ):
            return True

    return False
