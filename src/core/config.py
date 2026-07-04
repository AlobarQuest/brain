import re
from enum import Enum
from functools import lru_cache

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class BrainType(str, Enum):
    APP = "app"
    INFRA = "infra"
    OPEN = "open"
    CODE = "code"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    brain_type: BrainType
    mcp_access_key: str
    contributor_key: str | None = None
    log_level: str = "INFO"
    app_env: str = "production"
    port: int = 8000
    postgres_host: str
    postgres_port: int = 5432
    postgres_user: str
    postgres_password: str
    postgres_db: str
    openrouter_api_key: str | None = None
    database_url: str | None = None
    onboard_concurrency: int = 6

    @field_validator("mcp_access_key", "contributor_key")
    @classmethod
    def _hex64(cls, v: str | None, info) -> str | None:
        if v is not None and not _HEX64.match(v):
            raise ValueError(f"{info.field_name} must be 64 lowercase hex chars")
        return v

    @model_validator(mode="after")
    def _keys_must_differ(self) -> "Settings":
        if self.contributor_key is not None and self.contributor_key == self.mcp_access_key:
            raise ValueError(
                "contributor_key must not equal mcp_access_key "
                "(this would let every contributor act as approver)"
            )
        return self

    def effective_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache
def get_settings() -> "Settings":
    return Settings()
