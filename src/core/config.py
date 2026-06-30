import re
from enum import Enum
from functools import lru_cache
from pydantic import field_validator
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

    @field_validator("mcp_access_key")
    @classmethod
    def _hex64(cls, v: str) -> str:
        if not _HEX64.match(v):
            raise ValueError("mcp_access_key must be 64 lowercase hex chars")
        return v

    def effective_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return (f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
                f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}")

@lru_cache
def get_settings() -> "Settings":
    return Settings()
