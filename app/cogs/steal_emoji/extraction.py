from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import discord

from .constants import (
    CDN_EMOJI_URL,
    CUSTOM_EMOJI_PATTERN,
    FORWARDED_EMOJI_PATTERN,
    RAW_EMOJI_URL_PATTERN,
    logger,
)
from .models import StealItem


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

    is_animated = (
        animated
        if animated is not None
        else (extension.lower() == "gif" or query.get("animated", ["false"])[0].lower() == "true")
    )

    if is_animated:
        url = f"{CDN_EMOJI_URL}/{emoji_id}.gif?size=4096&quality=lossless"
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
                url=(f"{CDN_EMOJI_URL}/{emoji_id}.{extension}?size=4096&quality=lossless"),
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
        "[STEAL] Inspecting message | id=%s | author=%s | channel=%s | embeds=%d | stickers=%d",
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
