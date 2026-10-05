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
    ai_model_url: str = ""
    ai_model_timeout: int = 30

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )


settings = Settings()