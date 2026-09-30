from app.bot.client import Kitsu
from app.config import settings


def main():
    bot = Kitsu()
    bot.run(settings.discord_token)


if __name__ == "__main__":
    main()