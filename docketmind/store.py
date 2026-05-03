"""Async SQLAlchemy engine, session factory, and ORM models for DocketMind."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
    event,
    func,
    select,
    text,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, MappedAsDataclass, mapped_column, relationship

from docketmind.configure import settings

engine = create_async_engine(
    f"sqlite+aiosqlite:///{settings.db_path}",
    echo=False,
)


@event.listens_for(engine.sync_engine, "connect")
def _configure_sqlite(dbapi_connection, _connection_record):
    """Apply SQLite PRAGMAs that prevent 'database is locked' under concurrency.

    - WAL lets readers run while a single writer commits, eliminating most
      reader/writer contention.
    - busy_timeout makes other writers wait briefly instead of failing
      immediately when the write lock is held.
    - synchronous=NORMAL is the recommended pairing with WAL.
    - foreign_keys=ON enforces our ForeignKey constraints (off by default).
    """
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()


async_session: async_sessionmaker[AsyncSession] = async_sessionmaker(
    engine,
    expire_on_commit=False,
)


class Base(MappedAsDataclass, DeclarativeBase):
    """Base class for all ORM models."""


class Case(Base):
    """A tracked federal lawsuit and its associated metadata."""

    __tablename__ = "cases"

    court_listener_id: Mapped[str] = mapped_column(String, unique=True, index=True)
    name: Mapped[str] = mapped_column(String)

    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, init=False, default_factory=lambda: str(uuid.uuid4())
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        init=False,
        default_factory=lambda: datetime.now(UTC),
    )
    entries: Mapped[list["DocketEntry"]] = relationship(
        "DocketEntry",
        back_populates="case",
        cascade="all, delete-orphan",
        init=False,
        default_factory=list,
        repr=False,
    )

    @property
    def rss_url(self) -> str:
        """CourtListener RSS feed URL derived from court_listener_id."""
        return f"https://www.courtlistener.com/docket/{self.court_listener_id}/feed/"


class DocketEntry(Base):
    """A single entry in a federal court docket."""

    __tablename__ = "docket_entries"
    __table_args__ = (
        UniqueConstraint("case_id", "court_listener_id", name="uq_docket_entry_case_cl_id"),
    )

    case_id: Mapped[str] = mapped_column(String(36), ForeignKey("cases.id"), index=True)
    court_listener_id: Mapped[str] = mapped_column(String, index=True)
    title: Mapped[str] = mapped_column(String)
    content: Mapped[str] = mapped_column(String)
    content_hash: Mapped[str] = mapped_column(String(64))
    date_filed: Mapped[datetime] = mapped_column(DateTime)

    embedded: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("0"))

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, init=False, default_factory=lambda: str(uuid.uuid4())
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        init=False,
        default_factory=lambda: datetime.now(UTC),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        init=False,
        default_factory=lambda: datetime.now(UTC),
    )
    case: Mapped["Case"] = relationship("Case", back_populates="entries", init=False, repr=False)
    documents: Mapped[list["DocketEntryDocument"]] = relationship(
        "DocketEntryDocument",
        back_populates="entry",
        cascade="all, delete-orphan",
        init=False,
        default_factory=list,
        repr=False,
    )


class DocketEntryDocument(Base):
    """A PDF document attached to a docket entry."""

    __tablename__ = "docket_entry_documents"
    __table_args__ = (UniqueConstraint("docket_entry_id", "pdf_url", name="uq_doc_entry_url"),)

    docket_entry_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("docket_entries.id"), index=True
    )
    pdf_url: Mapped[str] = mapped_column(String)

    pdf_path: Mapped[str | None] = mapped_column(String, default=None)
    downloaded: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("0"))
    embedded: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("0"))

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, init=False, default_factory=lambda: str(uuid.uuid4())
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        init=False,
        default_factory=lambda: datetime.now(UTC),
    )
    entry: Mapped["DocketEntry"] = relationship(
        "DocketEntry", back_populates="documents", init=False, repr=False
    )


async def get_case(session: AsyncSession, case_id: str) -> Case | None:
    """Fetch a Case by primary key, or None if not found."""
    return await session.get(Case, case_id)


async def get_case_by_court_listener_id(
    session: AsyncSession, court_listener_id: str
) -> Case | None:
    """Fetch a Case by CourtListener docket ID, or None if not tracked."""
    result = await session.execute(select(Case).where(Case.court_listener_id == court_listener_id))
    return result.scalar_one_or_none()


async def list_cases(session: AsyncSession) -> list[Case]:
    """Return all tracked cases ordered by creation time."""
    result = await session.execute(select(Case).order_by(Case.created_at))
    return list(result.scalars())


async def list_entries_for_case(session: AsyncSession, case_id: str) -> list[DocketEntry]:
    """Return all docket entries for a case."""
    result = await session.execute(select(DocketEntry).where(DocketEntry.case_id == case_id))
    return list(result.scalars())


async def list_documents_for_entry(
    session: AsyncSession, entry_id: str
) -> list[DocketEntryDocument]:
    """Return all documents attached to a docket entry."""
    result = await session.execute(
        select(DocketEntryDocument).where(DocketEntryDocument.docket_entry_id == entry_id)
    )
    return list(result.scalars())


async def list_pending_downloads(session: AsyncSession, case_id: str) -> list[DocketEntryDocument]:
    """Return documents for a case that have not yet been downloaded."""
    result = await session.execute(
        select(DocketEntryDocument)
        .join(DocketEntry)
        .where(DocketEntry.case_id == case_id)
        .where(DocketEntryDocument.downloaded.is_(False))
    )
    return list(result.scalars())


async def list_unembedded_entries(session: AsyncSession, case_id: str) -> list[DocketEntry]:
    """Return docket entries for a case that have not yet been embedded."""
    result = await session.execute(
        select(DocketEntry)
        .where(DocketEntry.case_id == case_id)
        .where(DocketEntry.embedded.is_(False))
    )
    return list(result.scalars())


async def list_unembedded_documents(
    session: AsyncSession, case_id: str
) -> list[DocketEntryDocument]:
    """Return downloaded documents for a case that have not yet been embedded.

    Eagerly loads the parent DocketEntry so callers can access entry
    metadata (e.g. date_filed) without an extra query.
    """
    from sqlalchemy.orm import joinedload

    result = await session.execute(
        select(DocketEntryDocument)
        .join(DocketEntry)
        .options(joinedload(DocketEntryDocument.entry))
        .where(DocketEntry.case_id == case_id)
        .where(DocketEntryDocument.downloaded.is_(True))
        .where(DocketEntryDocument.embedded.is_(False))
    )
    return list(result.scalars())


__all__ = [
    "Base",
    "Case",
    "DocketEntry",
    "DocketEntryDocument",
    "async_session",
    "engine",
    "get_case",
    "get_case_by_court_listener_id",
    "list_cases",
    "list_entries_for_case",
    "list_documents_for_entry",
    "list_pending_downloads",
    "list_unembedded_entries",
    "list_unembedded_documents",
]
