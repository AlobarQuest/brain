from src.core.db import make_engine, make_sessionmaker


def test_engine_pool_config():
    e = make_engine("postgresql+asyncpg://u:p@h:5432/d")
    assert e.pool.size() == 10
    assert make_sessionmaker(e) is not None
