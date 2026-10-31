"""Application settings loaded from the environment via pydantic-settings."""

from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-backed configuration for the expense bot."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    APP_ENV: str = "development"
    LOG_LEVEL: str = "INFO"
    DATABASE_URL: str = "sqlite:///./data/expense.db"
    TIMEZONE: str = "Asia/Karachi"

    ALLOWED_NUMBERS: str = ""
    OWNER_NAME: str = ""

    WA_ACCESS_TOKEN: SecretStr = SecretStr("")
    WA_PHONE_NUMBER_ID: str = ""
    WA_BUSINESS_ACCOUNT_ID: str = ""
    WA_VERIFY_TOKEN: str = ""
    WA_APP_SECRET: SecretStr = SecretStr("")
    GRAPH_API_VERSION: str = "v23.0"

    OPENROUTER_API_KEY: SecretStr = SecretStr("")
    LLM_MODEL: str = "google/gemini-2.5-flash-lite"
    LLM_FALLBACK_MODEL: str = "google/gemini-3.1-flash-lite"
    TRANSCRIBE_MODEL: str = "google/gemini-2.5-flash-lite"
    HISTORY_TURNS: int = 6

    @property
    def allowed_numbers(self) -> frozenset[str]:
        """Allowed phone numbers reduced to digits only."""
        digits = ("".join(c for c in part if c.isdigit()) for part in self.ALLOWED_NUMBERS.split(","))
        return frozenset(number for number in digits if number)

    @property
    def is_sqlite(self) -> bool:
        """True when the configured database is SQLite."""
        return self.DATABASE_URL.startswith("sqlite")

    @property
    def is_production(self) -> bool:
        """True when running in the production environment."""
        return self.APP_ENV.lower() == "production"


@lru_cache
def get_settings() -> Settings:
    """Return the cached settings instance."""
    return Settings()


settings = get_settings()
