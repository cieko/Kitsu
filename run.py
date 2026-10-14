import logging
import threading

from waitress import serve

from app.api.app import app
from app.bot.client import Kitsu
from app.config import settings


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


def run_web_server():
    # app.run(
    #     host=settings.flask_host,
    #     port=settings.flask_port,
    # )
    serve(
        app,
        host=settings.flask_host,
        port=settings.flask_port,
        threads=4,
    )


def main():
    web_thread = threading.Thread(
        target=run_web_server,
        name="kitsu-web-server",
        daemon=True,
    )
    web_thread.start()

    bot = Kitsu()
    bot.run(settings.discord_token)


if __name__ == "__main__":
    main()