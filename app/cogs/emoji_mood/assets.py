from __future__ import annotations

import emoji

from app.cogs.steal_emoji.extraction import get_message_items
from app.services.emoji_mood.models import MoodAsset


def message_assets(message) -> list[MoodAsset]:
    content = message.content or ""
    texts = [content]
    snapshots = getattr(message, "message_snapshots", None) or []
    texts.extend(getattr(snapshot, "content", None) or "" for snapshot in snapshots)
    items = []
    if (
        message.stickers or message.embeds or snapshots
        or any("<:" in text or "<a:" in text or "discordapp.com/emojis/" in text for text in texts)
    ):
        for item in get_message_items(message):
            url = item.url
            if item.item_type == "emoji":
                extension = "gif" if item.animated else "png"
                url = f"https://cdn.discordapp.com/emojis/{item.item_id}.{extension}?size=128&quality=lossless"
            items.append(MoodAsset(item.item_type, item.name, item.item_id, url, item.source_format))
    for text in texts:
        for match in emoji.emoji_list(text):
            symbol = match["emoji"]
            items.append(MoodAsset("unicode", emoji.demojize(symbol).strip(":"), unicode=symbol))
    return list({item.key: item for item in items}.values())
