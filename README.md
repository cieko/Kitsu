<img align="center" alt="Kitsu" src="./kitsu_readme.jpg" />

# Kitsu

Discord bot for sticky messages, temporary channels, emoji/sticker management,
scheduled messages, and Ghosty tracking.

## Setup

Requires Python 3.10+ and MongoDB; Ghosty needs transaction support.

```sh
python -m pip install -r requirements.txt
```

Create `.env`:

```env
DISCORD_TOKEN=
DISCORD_APPLICATION_ID=
DISCORD_CLIENT_ID=
DISCORD_MAIN_GUILD_ID=
DATABASE_URL=
```

Enable **Server Members** and **Message Content** intents in Discord, then start:

```sh
python run.py
```

Emoji mood, NSFW, and nudity classification is currently paused.

Tests: `python -m pytest -q`.
