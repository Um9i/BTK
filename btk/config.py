"""Application settings, loaded from environment variables / .env."""

from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BTK_", env_file=".env", extra="ignore")

    # Postgres
    db_host: str = "localhost"
    db_port: int = 5432
    db_name: str = "btk"
    db_user: str = "btk"
    db_password: SecretStr = SecretStr("btk")
    db_pool_min_size: int = 1
    db_pool_max_size: int = 10

    # Discord bot
    discord_token: SecretStr = SecretStr("")
    discord_command_prefix: str = "!"

    # Live game (the current round only, at fixed URLs; the actual round
    # number/tick come from the status page / each file's own "Tick:"
    # header, not config -- see btk/dumps/status.py). Matches merlin.cfg's
    # [URL] section (game/dumps/ships).
    dump_live_base_url: str = "https://game.planetarion.com/botfiles"
    ships_live_url: str = "https://game.planetarion.com/api.pl?stats"
    game_status_url: str = "https://www.planetarion.com/games/status/game/"

    # API
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    @property
    def db_dsn(self) -> str:
        return (
            f"postgresql://{self.db_user}:{self.db_password.get_secret_value()}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
