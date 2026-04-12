"""Shared SQLAlchemy ORM models for DocketMind.

Imported by both ingestion and intelligence packages — neither depends on the other.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, event, func, inspect, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Base class for all ORM models."""


@event.listens_for(Base, "init", propagate=True)
def _apply_column_defaults(target: Base, args: tuple, kwargs: dict) -> None:
    """Apply scalar column defaults on __init__ for non-dataclass ORM models.

    SQLAlchemy's non-dataclass declarative style only applies column defaults at
    INSERT time.  This listener populates Python-side scalar defaults eagerly so
    that freshly constructed instances behave as expected without a database round
    trip.
    """
    mapper = inspect(type(target))
    for col_attr in mapper.column_attrs:
        col = col_attr.columns[0]
        attr_name = col_attr.key
        if attr_name not in kwargs and col.default is not None and col.default.is_scalar:
            kwargs[attr_name] = col.default.arg


class Case(Base):
    """A tracked federal lawsuit and its associated metadata."""

    __tablename__ = "cases"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    court_listener_id: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    court: Mapped[str] = mapped_column(String, nullable=False)
    memory_text: Mapped[str | None] = mapped_column(String, nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    entries: Mapped[list["DocketEntry"]] = relationship(
        "DocketEntry", back_populates="case", cascade="all, delete-orphan"
    )

    @property
    def rss_url(self) -> str:
        """CourtListener RSS feed URL derived from court_listener_id."""
        return f"https://www.courtlistener.com/docket/{self.court_listener_id}/feed/"


class DocketEntry(Base):
    """A single entry in a federal court docket."""

    __tablename__ = "docket_entries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    case_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("cases.id"), nullable=False, index=True
    )
    court_listener_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str] = mapped_column(String, nullable=False, default="")
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    embedded: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("0")
    )
    date_filed: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    case: Mapped["Case"] = relationship("Case", back_populates="entries")
    documents: Mapped[list["DocketEntryDocument"]] = relationship(
        "DocketEntryDocument", back_populates="entry", cascade="all, delete-orphan"
    )


class DocketEntryDocument(Base):
    """A PDF document attached to a docket entry."""

    __tablename__ = "docket_entry_documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    docket_entry_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("docket_entries.id"), nullable=False, index=True
    )
    pdf_url: Mapped[str] = mapped_column(String, nullable=False)
    pdf_path: Mapped[str | None] = mapped_column(String, nullable=True)
    downloaded: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("0")
    )
    embedded: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("0")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    entry: Mapped["DocketEntry"] = relationship("DocketEntry", back_populates="documents")
