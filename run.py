import threading

from app.api.app import app
from app.bot.client import Kitsu
from app.config import settings


def run_web_server():
    app.run(
        host=settings.flask_host,
        port=settings.flask_port,
    )


def main():
    web_thread = threading.Thread(
        target=run_web_server,
        daemon=True,
    )
    web_thread.start()

    bot = Kitsu()
    bot.run(settings.discord_token)


if __name__ == "__main__":
    main()