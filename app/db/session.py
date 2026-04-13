from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import Settings
from app.db.base import Base


def make_engine(settings: Settings):
    return create_async_engine(settings.database_url, echo=False)


def _migrate_sqlite_sync(conn) -> None:
    """Добавляет колонки в существующие SQLite-таблицы (create_all их не обновляет)."""
    insp = inspect(conn)
    tables = insp.get_table_names()
    if "orders" in tables:
        cols = {c["name"] for c in insp.get_columns("orders")}
        if "trend_urls_json" not in cols:
            conn.execute(text("ALTER TABLE orders ADD COLUMN trend_urls_json TEXT"))
    if "generations" in tables:
        cols = {c["name"] for c in insp.get_columns("generations")}
        if "reference_video_url" not in cols:
            conn.execute(text("ALTER TABLE generations ADD COLUMN reference_video_url TEXT"))


async def init_db(engine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if "sqlite" in str(engine.url).lower():
            await conn.run_sync(_migrate_sqlite_sync)


def make_session_factory(engine):
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_session(session_factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session
