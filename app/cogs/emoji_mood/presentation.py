import discord

from app.services.emoji_mood.models import MoodAsset, MoodResult


LABELS = {"bully": "Teasing / bully", "fun": "Fun / playful", "unknown": "Unclear"}


def safe_text(value: str) -> str:
    return discord.utils.escape_mentions(discord.utils.escape_markdown(value))


def result_embed(asset: MoodAsset, result: MoodResult) -> discord.Embed:
    assessments = (result.nsfw, result.nudity)
    colour = 0xED4245 if "likely" in assessments else 0xFEE75C if "uncertain" in assessments else 0x7DBBEB
    embed = discord.Embed(title="Emoji mood", colour=colour)
    name = asset.unicode if asset.kind == "unicode" else asset.name[:80]
    embed.add_field(name="Emoji / sticker", value=safe_text(name), inline=False)
    labels = [LABELS.get(mood, mood.title()) for mood in result.moods]
    if result.nsfw == "likely":
        labels.insert(0, "NSFW")
    embed.add_field(name="Mood", value=" · ".join(labels), inline=True)
    embed.add_field(name="NSFW", value=result.nsfw.title(), inline=True)
    embed.add_field(name="Nudity", value=result.nudity.title(), inline=True)
    embed.add_field(name="Why", value=safe_text(result.reason), inline=False)
    if asset.kind == "unicode":
        note = "Based on the Unicode emoji's standard meaning; context may change it."
    elif result.total_frames > 1:
        note = f"{result.sampled_frames} of {result.total_frames} animation frames checked; brief details can be missed."
    else:
        note = "Based on appearance; this does not determine the sender's intent."
    embed.set_footer(text=note)
    return embed
