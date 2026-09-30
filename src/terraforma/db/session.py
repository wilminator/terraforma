"""Connecting to the database: one async engine per app."""

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from .base import Base


def make_engine(url: str) -> AsyncEngine:
    # pool_pre_ping: MySQL drops idle connections; check before use.
    return create_async_engine(url, pool_pre_ping=True)


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def create_tables(engine: AsyncEngine) -> None:
    """Every engine table, for tests and first runs (Alembic takes over in the skeleton phase)."""
    # Import the models so their tables are registered on Base.metadata.
    from .. import models  # noqa: F401

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


async def drop_tables(engine: AsyncEngine) -> None:
    from .. import models  # noqa: F401

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
