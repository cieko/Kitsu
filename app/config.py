from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    discord_token: str
    discord_application_id: str
    discord_client_id: str
    discord_main_guild_id: str

    database_url: str = ""

    flask_host: str = "0.0.0.0"
    flask_port: int = 5000

    ai_model_name: str = "kitsu_mod"
    ai_model_url: str = "http://127.0.0.1:11434"
    ai_model_timeout: int = 30
    ai_model_num_gpu: int | None = None

    emoji_mood_enabled: bool = False
    emoji_mood_model: str = "gemma3:latest"
    emoji_mood_model_url: str = "http://127.0.0.1:11434"
    emoji_mood_timeout: int = 90
    emoji_mood_num_gpu: int | None = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )


settings = Settings()
