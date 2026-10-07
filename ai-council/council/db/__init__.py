"""Async SQLAlchemy database engine; PostgreSQL is the source of truth."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from council.config import settings

_engine: AsyncEngine | None = None


def _async_url(url: str) -> str:
    return url.replace("postgresql://", "postgresql+asyncpg://", 1)


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_async_engine(_async_url(settings.database_url), pool_pre_ping=True)
    return _engine


@asynccontextmanager
async def connect() -> AsyncIterator[AsyncConnection]:
    async with get_engine().connect() as connection:
        yield connection


@asynccontextmanager
async def transaction() -> AsyncIterator[AsyncConnection]:
    async with get_engine().begin() as connection:
        yield connection


async def close_engine() -> None:
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None
