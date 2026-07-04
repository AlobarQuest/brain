import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from src.brains.app import models  # noqa: F401 — ensure models are registered with Base.metadata
from src.core.db import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# Self-contained URL resolution — reads from env or get_settings()
database_url = os.environ.get("DATABASE_URL")
if not database_url:
    fallback = config.get_main_option("sqlalchemy.url")
    if fallback and fallback != "driver://user:pass@localhost/dbname":
        database_url = fallback
if not database_url:
    from src.core.config import get_settings

    database_url = get_settings().effective_database_url()


def run_migrations_offline() -> None:
    context.configure(
        url=database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = create_async_engine(database_url)
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
