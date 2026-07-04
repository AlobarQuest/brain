from functools import lru_cache

from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import StaticPool


class Base(DeclarativeBase):
    pass


def make_engine(url: str) -> AsyncEngine:
    # SQLite (used in tests) does not support pool_size / max_overflow.
    if url.startswith("sqlite"):
        return create_async_engine(
            url,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
    return create_async_engine(
        url, pool_size=10, max_overflow=20, pool_pre_ping=True, pool_recycle=3600
    )


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker:
    return async_sessionmaker(engine, expire_on_commit=False)


@lru_cache
def get_engine() -> AsyncEngine:
    from src.core.config import get_settings

    return make_engine(get_settings().effective_database_url())


@lru_cache
def get_session_factory() -> async_sessionmaker:
    return async_sessionmaker(get_engine(), expire_on_commit=False, autoflush=False)
