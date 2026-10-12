import asyncio
import sys
from pathlib import Path

# Add the project root to Python's import path.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.bot.client import Kitsu
from app.config import settings
from app.database.repositories.schedule_repository import (
    add_schedule,
    set_timezone,
)
from app.services.emoji_services import get_application_emoji


CHANNEL_ID = 1553844187902771290
ROLE_ID = 1547766491842351127

COLOR = "#FAC0E4"
FOOTER = "do ?date to claim it asap"

# IST time, goos amount
GHOSTY_SCHEDULES = [
    ("06:31", "2"),
    ("06:41", "2"),
    ("07:32", "2"),
    ("07:52", "2"),
    ("08:33", "3"),
    ("09:03", "3"),
    ("09:34", "4"),
    ("10:14", "4"),
    ("10:35", "5"),
    ("11:25", "5"),
    ("11:36", "6"),
    ("12:37", "7"),
    ("13:38", "8"),
    ("14:39", "9"),
    ("15:31", "10"),
    ("15:40", "10"),
    ("16:41", "11"),
    ("17:42", "12"),
    ("17:51", "12"),
    ("18:43", "1"),
    ("19:01", "1"),
    ("19:07", "13"),
    ("19:44", "2"),
    ("20:11", "2"),
    ("20:45", "3"),
    ("21:21", "3"),
    ("21:46", "4"),
    ("22:47", "5"),
    ("23:48", "6"),
    ("00:49", "7"),
    ("01:32", "8"),
    ("01:50", "8"),
    ("02:42", "9"),
    ("02:51", "9"),
    ("03:52", "22"),
    ("04:53", "11"),
    ("05:02", "11"),
    ("05:30", "50"),
]


async def main():
    bot = Kitsu()

    try:
        await bot.login(settings.discord_token)

        # Fetch the application emoji using the reusable emoji service.
        goos = await get_application_emoji(
            bot,
            "goos",
            "<:goos:1558941112247394334>",
        )

        # Configure the server timezone as UTC.
        guild_id = 1546948804123697212

        await set_timezone(guild_id, "Asia/Kolkata")

        for time, amount in GHOSTY_SCHEDULES:
            if time == "05:30":
                description = (
                    "The reset is here! Claim your 50 goos now using ?date. "
                    "The longer you wait, the fewer goos you'll receive, "
                    "so don't delay!"
                )
            else:
                description = "You have less than 1 minute."

            message = (
                f"{{content: {goos} {amount} goos <@&{ROLE_ID}>}}\n"
                "{embed}\n"
                f"{{color: {COLOR}}}\n"
                f"{{description: {description}}}\n"
                f"{{footer: {FOOTER}}}"
            )

            await add_schedule(
                guild_id=guild_id,
                channel_id=CHANNEL_ID,
                time=time,
                message=message,
                name=f"Ghosty {time}",
            )

            print(
                f"Added Ghosty schedule at {time} UTC "
                f"({amount} goos)"
            )

    finally:
        await bot.close()


if __name__ == "__main__":
    asyncio.run(main())
