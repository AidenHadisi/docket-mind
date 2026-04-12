"""Async SQLAlchemy engine and session factory."""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from docketmind.config import settings

engine = create_async_engine(
    f"sqlite+aiosqlite:///{settings.db_path}",
    echo=False,
)

async_session: async_sessionmaker[AsyncSession] = async_sessionmaker(
    engine,
    expire_on_commit=False,
)
