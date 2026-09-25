from functools import lru_cache
from pydantic import Field, field_validator

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"

    # The app connects with a read-only role (see db/init.sql).
    database_url: str = "postgresql+psycopg://insight_reader:reader_pw@localhost:5432/insightbot"
    # Use "memory://" to run without Redis (history is then lost on restart).
    redis_url: str = "redis://localhost:6379/0"

    max_rows: int = Field(default=200, gt=0)
    statement_timeout_ms: int = Field(default=5000, gt=0)
    history_max_messages: int = Field(default=20, ge=2, multiple_of=2)
    history_ttl_seconds: int = Field(default=86400, gt=0)
    agent_max_steps: int = Field(default=10, gt=0)

    @field_validator("openai_api_key")
    @classmethod
    def strip_key(cls, value: str) -> str:
        return value.strip()


@lru_cache
def get_settings() -> Settings:
    return Settings()
