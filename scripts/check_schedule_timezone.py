
import asyncio
import sys
from pathlib import Path

# Add the project root to Python's import path.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.database.repositories.schedule_repository import (
    get_timezone,
    get_schedules,
)

GUILD_ID = 1546948804123697212


async def main():
    try:
        timezone = await get_timezone(GUILD_ID)
        schedules = await get_schedules(GUILD_ID)

        print(f"\nServer timezone: {timezone}")
        print(f"Total schedules: {len(schedules)}")
        print("\nSaved schedule times:")

        for schedule in sorted(
            schedules,
            key=lambda item: item.get("time", ""),
        ):
            print(
                f'{schedule.get("name", "Unnamed")}: '
                f'{schedule.get("time")} '
                f'| Last sent: {schedule.get("last_sent_date")}'
            )

    except Exception as error:
        print(f"Database check failed: {type(error).__name__}: {error}")


if __name__ == "__main__":
    asyncio.run(main())