# Ingestion Package Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the full `docketmind/ingestion/` package — RSS polling, PDF downloading, vector indexing, memory updates, and APScheduler orchestration — backed by SQLAlchemy models and Alembic migrations.

**Architecture:** Each case gets its own named APScheduler interval job. On every tick, `sync_case()` fetches the full CourtListener RSS feed, reconciles docket entries and PDF documents against SQLite, downloads new PDFs, embeds new/changed content via LlamaIndex, and updates the case memory via OpenAI. All state lives on disk — SQLite for metadata, LlamaIndex SimpleVectorStore for embeddings.

**Tech Stack:** Python 3.13, SQLAlchemy 2.0 async, aiosqlite, Alembic, LlamaIndex core + OpenAI embeddings/LLM, httpx, feedparser, aiofiles, stamina, APScheduler 3.x, Pydantic v2, Loguru, pytest + pytest-asyncio + respx.

---

## File Map

| File | Role |
|------|------|
| `docketmind/models.py` | SQLAlchemy ORM: `Case`, `DocketEntry`, `DocketEntryDocument` |
| `docketmind/db.py` | Async engine + session factory |
| `alembic.ini` | Alembic config (URL set at runtime in env.py) |
| `alembic/env.py` | Async Alembic env — imports `Base` from `models.py` |
| `alembic/script.py.mako` | Migration file template |
| `alembic/versions/` | Generated migration files |
| `docketmind/ingestion/__init__.py` | Public re-exports: `sync_case`, `start`, `add_case`, `remove_case` |
| `docketmind/ingestion/rss.py` | `RawEntry` Pydantic model + `fetch_feed()` |
| `docketmind/ingestion/downloader.py` | `download_pdf()` with stamina retry |
| `docketmind/ingestion/indexer.py` | LlamaIndex `get_index()`, `upsert_entry()`, `upsert_document()` |
| `docketmind/ingestion/memory.py` | `update_case_memory()` via OpenAI chat |
| `docketmind/ingestion/pipeline.py` | `SyncResult` + `sync_case()` orchestrator |
| `docketmind/ingestion/scheduler.py` | `start()`, `add_case()`, `remove_case()` |
| `tests/__init__.py` | Makes tests a package |
| `tests/ingestion/__init__.py` | Makes tests/ingestion a package |
| `tests/test_models.py` | Model instantiation + `rss_url` property |
| `tests/ingestion/test_rss.py` | RSS fetch + parse with mocked HTTP |
| `tests/ingestion/test_downloader.py` | PDF download with mocked HTTP |
| `tests/ingestion/test_indexer.py` | LlamaIndex upsert with temp dir |
| `tests/ingestion/test_memory.py` | Memory update with mocked OpenAI |
| `tests/ingestion/test_pipeline.py` | Full pipeline with mocked components |
| `tests/ingestion/test_scheduler.py` | Scheduler job registration/removal |

---

## Task 1: SQLAlchemy Models

**Files:**
- Create: `docketmind/models.py`
- Create: `tests/__init__.py`
- Create: `tests/test_models.py`

- [ ] **Step 1: Create test package init files**

```bash
touch tests/__init__.py tests/ingestion/__init__.py tests/bot/__init__.py tests/intelligence/__init__.py
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_models.py`:

```python
"""Tests for SQLAlchemy ORM models."""

from datetime import datetime, timezone

from docketmind.models import Case, DocketEntry, DocketEntryDocument


def test_case_rss_url_derived_from_court_listener_id():
    case = Case(
        court_listener_id="12345678",
        name="United States v. Doe",
        court="D. Mass.",
    )
    assert case.rss_url == "https://www.courtlistener.com/docket/12345678/feed/"


def test_docket_entry_defaults_embedded_false():
    entry = DocketEntry(
        case_id="some-uuid",
        court_listener_id="entry-001",
        title="Order on Motion",
        content="Court grants motion to dismiss.",
        content_hash="abc123",
        date_filed=datetime(2026, 1, 15, tzinfo=timezone.utc),
    )
    assert entry.embedded is False


def test_docket_entry_document_defaults():
    doc = DocketEntryDocument(
        docket_entry_id="entry-uuid",
        pdf_url="https://storage.courtlistener.com/recap/doc.pdf",
    )
    assert doc.downloaded is False
    assert doc.embedded is False
    assert doc.pdf_path is None
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
uv run pytest tests/test_models.py -v
```

Expected: `ImportError: cannot import name 'Case' from 'docketmind.models'`

- [ ] **Step 4: Create `docketmind/models.py`**

```python
"""Shared SQLAlchemy ORM models for DocketMind.

Imported by both ingestion and intelligence packages — neither depends on the other.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Base class for all ORM models."""


class Case(Base):
    """A tracked federal lawsuit and its associated metadata."""

    __tablename__ = "cases"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
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

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    case_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("cases.id"), nullable=False, index=True
    )
    court_listener_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str] = mapped_column(String, nullable=False, default="")
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    embedded: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("0"))
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

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    docket_entry_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("docket_entries.id"), nullable=False, index=True
    )
    pdf_url: Mapped[str] = mapped_column(String, nullable=False)
    pdf_path: Mapped[str | None] = mapped_column(String, nullable=True)
    downloaded: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("0"))
    embedded: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    entry: Mapped["DocketEntry"] = relationship("DocketEntry", back_populates="documents")
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
uv run pytest tests/test_models.py -v
```

Expected: 3 passed

- [ ] **Step 6: Commit**

```bash
git add docketmind/models.py tests/test_models.py tests/__init__.py tests/ingestion/__init__.py tests/bot/__init__.py tests/intelligence/__init__.py
git commit -m "feat: add SQLAlchemy ORM models"
```

---

## Task 2: Database Session + Alembic

**Files:**
- Create: `docketmind/db.py`
- Create: `alembic.ini`
- Create: `alembic/env.py`
- Create: `alembic/script.py.mako`
- Create: `alembic/versions/` (directory)

- [ ] **Step 1: Create `docketmind/db.py`**

```python
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
```

- [ ] **Step 2: Remove the placeholder and create Alembic config**

```bash
rm alembic/.gitkeep
mkdir -p alembic/versions
```

Create `alembic.ini` in the project root:

```ini
[alembic]
script_location = alembic
prepend_sys_path = .

[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARN
handlers = console
qualname =

[logger_sqlalchemy]
level = WARN
handlers =
qualname = sqlalchemy.engine

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
datefmt = %H:%M:%S
```

- [ ] **Step 3: Create `alembic/script.py.mako`**

```
"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

# revision identifiers, used by Alembic.
revision: str = ${repr(up_revision)}
down_revision: Union[str, None] = ${repr(down_revision)}
branch_labels: Union[str, Sequence[str], None] = ${repr(branch_labels)}
depends_on: Union[str, Sequence[str], None] = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
```

- [ ] **Step 4: Create `alembic/env.py`**

```python
"""Alembic environment configuration with async SQLAlchemy support."""

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

from docketmind.config import settings
from docketmind.models import Base

config = context.config
config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{settings.db_path}")

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations without a live database connection."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    """Run migrations with an active connection."""
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Create async engine and run migrations."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    """Entry point for online migration mode."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

- [ ] **Step 5: Ensure `data/` directory exists and generate the initial migration**

```bash
mkdir -p data
uv run alembic revision --autogenerate -m "initial"
```

Expected: creates a file like `alembic/versions/xxxx_initial.py` with `CREATE TABLE` statements for `cases`, `docket_entries`, `docket_entry_documents`.

- [ ] **Step 6: Apply the migration**

```bash
uv run alembic upgrade head
```

Expected: `Running upgrade  -> xxxx, initial`

Verify: `data/docketmind.db` now exists.

- [ ] **Step 7: Write a smoke test for the DB session**

Add to `tests/test_models.py`:

```python
import pytest
from sqlalchemy import text
from docketmind.db import async_session, engine
from docketmind.models import Base, Case


@pytest.fixture(autouse=True)
async def setup_db():
    """Create tables in a fresh in-memory DB for each test."""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    import docketmind.db as db_module

    test_engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    db_module.engine = test_engine
    db_module.async_session = async_sessionmaker(test_engine, expire_on_commit=False)
    yield
    await test_engine.dispose()


async def test_case_can_be_saved_and_retrieved():
    async with async_session() as session:
        case = Case(
            court_listener_id="99999",
            name="Test v. Case",
            court="D. Mass.",
        )
        session.add(case)
        await session.commit()

    async with async_session() as session:
        from sqlalchemy import select
        result = await session.execute(select(Case).where(Case.court_listener_id == "99999"))
        saved = result.scalar_one()
        assert saved.name == "Test v. Case"
        assert saved.rss_url == "https://www.courtlistener.com/docket/99999/feed/"
```

- [ ] **Step 8: Run all model tests**

```bash
uv run pytest tests/test_models.py -v
```

Expected: 4 passed

- [ ] **Step 9: Commit**

```bash
git add docketmind/db.py alembic.ini alembic/env.py alembic/script.py.mako alembic/versions/ tests/test_models.py
git commit -m "feat: add database session and Alembic migrations"
```

---

## Task 3: RSS Fetcher

**Files:**
- Create: `docketmind/ingestion/__init__.py`
- Create: `docketmind/ingestion/rss.py`
- Create: `tests/ingestion/test_rss.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/ingestion/test_rss.py`:

```python
"""Tests for CourtListener RSS feed fetching and parsing."""

from datetime import datetime, timezone

import httpx
import pytest
import respx

from docketmind.ingestion.rss import RawEntry, fetch_feed

RSS_FIXTURE = """\
<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0">
  <channel>
    <title>United States v. Doe</title>
    <item>
      <title>Order GRANTING Motion to Dismiss</title>
      <guid isPermaLink="false">https://www.courtlistener.com/docket/12345/recaps/001</guid>
      <pubDate>Mon, 07 Apr 2026 12:00:00 +0000</pubDate>
      <description>&lt;p&gt;Court grants defendant&#39;s motion to dismiss.&lt;/p&gt;</description>
      <enclosure
        url="https://storage.courtlistener.com/recap/gov.uscourts.test.001.pdf"
        type="application/pdf"
        length="10000"/>
    </item>
    <item>
      <title>Notice of Appearance</title>
      <guid isPermaLink="false">https://www.courtlistener.com/docket/12345/recaps/002</guid>
      <pubDate>Tue, 08 Apr 2026 09:00:00 +0000</pubDate>
      <description>&lt;p&gt;Attorney files notice of appearance.&lt;/p&gt;</description>
    </item>
  </channel>
</rss>
"""


@respx.mock
async def test_fetch_feed_returns_parsed_entries():
    respx.get("https://www.courtlistener.com/docket/12345/feed/").mock(
        return_value=httpx.Response(200, text=RSS_FIXTURE)
    )

    entries = await fetch_feed("https://www.courtlistener.com/docket/12345/feed/")

    assert len(entries) == 2
    assert isinstance(entries[0], RawEntry)


@respx.mock
async def test_fetch_feed_extracts_pdf_urls():
    respx.get("https://www.courtlistener.com/docket/12345/feed/").mock(
        return_value=httpx.Response(200, text=RSS_FIXTURE)
    )

    entries = await fetch_feed("https://www.courtlistener.com/docket/12345/feed/")

    assert entries[0].pdf_urls == [
        "https://storage.courtlistener.com/recap/gov.uscourts.test.001.pdf"
    ]
    assert entries[1].pdf_urls == []


@respx.mock
async def test_fetch_feed_strips_html_from_content():
    respx.get("https://www.courtlistener.com/docket/12345/feed/").mock(
        return_value=httpx.Response(200, text=RSS_FIXTURE)
    )

    entries = await fetch_feed("https://www.courtlistener.com/docket/12345/feed/")

    assert "<p>" not in entries[0].content
    assert "Court grants" in entries[0].content


@respx.mock
async def test_fetch_feed_computes_content_hash():
    respx.get("https://www.courtlistener.com/docket/12345/feed/").mock(
        return_value=httpx.Response(200, text=RSS_FIXTURE)
    )

    entries = await fetch_feed("https://www.courtlistener.com/docket/12345/feed/")

    assert len(entries[0].content_hash) == 64  # sha256 hex digest


@respx.mock
async def test_fetch_feed_raises_on_http_error():
    respx.get("https://www.courtlistener.com/docket/12345/feed/").mock(
        return_value=httpx.Response(503)
    )

    with pytest.raises(httpx.HTTPStatusError):
        await fetch_feed("https://www.courtlistener.com/docket/12345/feed/")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/ingestion/test_rss.py -v
```

Expected: `ImportError: cannot import name 'RawEntry' from 'docketmind.ingestion.rss'`

- [ ] **Step 3: Create `docketmind/ingestion/__init__.py`**

```python
"""Ingestion package: RSS polling, PDF downloading, embedding, and memory updates."""
```

- [ ] **Step 4: Create `docketmind/ingestion/rss.py`**

```python
"""CourtListener RSS feed fetcher and parser."""

import hashlib
import re
from datetime import datetime, timezone

import feedparser
import httpx
from pydantic import BaseModel


class RawEntry(BaseModel):
    """A single docket entry parsed from a CourtListener RSS feed."""

    court_listener_id: str
    title: str
    content: str
    content_hash: str
    date_filed: datetime
    pdf_urls: list[str]


def _strip_html(html: str) -> str:
    """Remove HTML tags from a string and collapse whitespace."""
    text = re.sub(r"<[^>]+>", "", html)
    return re.sub(r"\s+", " ", text).strip()


def _compute_hash(title: str, content: str) -> str:
    """Compute a SHA-256 hash of title + content for change detection."""
    return hashlib.sha256(f"{title}\n{content}".encode()).hexdigest()


def _extract_pdf_urls(entry: feedparser.FeedParserDict) -> list[str]:
    """Extract CourtListener .pdf URLs from RSS entry enclosures and links."""
    urls: list[str] = []

    for enclosure in getattr(entry, "enclosures", []):
        url = enclosure.get("url", "")
        mime = enclosure.get("type", "")
        if mime == "application/pdf" and "courtlistener" in url:
            urls.append(url)

    for link in getattr(entry, "links", []):
        url = link.get("href", "")
        if url.endswith(".pdf") and "courtlistener" in url and url not in urls:
            urls.append(url)

    return urls


async def fetch_feed(rss_url: str) -> list[RawEntry]:
    """Fetch and parse a CourtListener RSS feed, returning all docket entries.

    Raises httpx.HTTPStatusError on non-2xx responses.
    """
    async with httpx.AsyncClient() as client:
        response = await client.get(rss_url, follow_redirects=True, timeout=30)
        response.raise_for_status()

    feed = feedparser.parse(response.text)

    entries: list[RawEntry] = []
    for item in feed.entries:
        content = _strip_html(getattr(item, "summary", "") or "")

        if item.get("published_parsed"):
            t = item.published_parsed
            date_filed = datetime(t[0], t[1], t[2], t[3], t[4], t[5], tzinfo=timezone.utc)
        else:
            date_filed = datetime.now(timezone.utc)

        entries.append(
            RawEntry(
                court_listener_id=item.get("id") or item.get("link", ""),
                title=item.get("title", ""),
                content=content,
                content_hash=_compute_hash(item.get("title", ""), content),
                date_filed=date_filed,
                pdf_urls=_extract_pdf_urls(item),
            )
        )

    return entries
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
uv run pytest tests/ingestion/test_rss.py -v
```

Expected: 5 passed

- [ ] **Step 6: Commit**

```bash
git add docketmind/ingestion/__init__.py docketmind/ingestion/rss.py tests/ingestion/test_rss.py
git commit -m "feat: add RSS feed fetcher and RawEntry model"
```

---

## Task 4: PDF Downloader

**Files:**
- Create: `docketmind/ingestion/downloader.py`
- Create: `tests/ingestion/test_downloader.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/ingestion/test_downloader.py`:

```python
"""Tests for PDF downloader."""

from pathlib import Path

import httpx
import pytest
import respx

from docketmind.ingestion.downloader import download_pdf

PDF_URL = "https://storage.courtlistener.com/recap/gov.uscourts.test.001.pdf"
FAKE_PDF = b"%PDF-1.4 fake pdf content"


@respx.mock
async def test_download_pdf_writes_file_to_disk(tmp_path: Path):
    dest = tmp_path / "test.pdf"
    respx.get(PDF_URL).mock(return_value=httpx.Response(200, content=FAKE_PDF))

    await download_pdf(PDF_URL, dest)

    assert dest.exists()
    assert dest.read_bytes() == FAKE_PDF


@respx.mock
async def test_download_pdf_creates_parent_directories(tmp_path: Path):
    dest = tmp_path / "case-uuid" / "subdir" / "test.pdf"
    respx.get(PDF_URL).mock(return_value=httpx.Response(200, content=FAKE_PDF))

    await download_pdf(PDF_URL, dest)

    assert dest.exists()


@respx.mock
async def test_download_pdf_raises_on_http_error(tmp_path: Path):
    dest = tmp_path / "test.pdf"
    respx.get(PDF_URL).mock(return_value=httpx.Response(403))

    with pytest.raises(httpx.HTTPStatusError):
        await download_pdf(PDF_URL, dest)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/ingestion/test_downloader.py -v
```

Expected: `ImportError: cannot import name 'download_pdf'`

- [ ] **Step 3: Create `docketmind/ingestion/downloader.py`**

```python
"""PDF downloader with stamina retry for transient failures."""

from pathlib import Path

import aiofiles
import httpx
import stamina


@stamina.retry(on=httpx.HTTPError, attempts=5)
async def download_pdf(url: str, dest: Path) -> None:
    """Download a PDF from url and write it to dest.

    Parent directories are created automatically. Retries up to 5 times
    on transient HTTP errors with exponential backoff via stamina.

    Raises httpx.HTTPStatusError if the server returns a non-2xx status.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)

    async with httpx.AsyncClient() as client:
        response = await client.get(url, follow_redirects=True, timeout=60)
        response.raise_for_status()

    async with aiofiles.open(dest, "wb") as f:
        await f.write(response.content)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/ingestion/test_downloader.py -v
```

Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add docketmind/ingestion/downloader.py tests/ingestion/test_downloader.py
git commit -m "feat: add PDF downloader with stamina retry"
```

---

## Task 5: Vector Store Indexer

**Files:**
- Create: `docketmind/ingestion/indexer.py`
- Create: `tests/ingestion/test_indexer.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/ingestion/test_indexer.py`:

```python
"""Tests for LlamaIndex vector store indexer."""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from docketmind.ingestion.indexer import get_index, upsert_entry
from docketmind.models import DocketEntry


@pytest.fixture
def tmp_index_path(tmp_path: Path, monkeypatch):
    """Point settings.index_path to a temp directory."""
    import docketmind.config as cfg
    monkeypatch.setattr(cfg.settings, "index_path", tmp_path / "index")
    return tmp_path / "index"


@pytest.fixture
def sample_entry() -> DocketEntry:
    return DocketEntry(
        id="entry-001",
        case_id="case-001",
        court_listener_id="cl-001",
        title="Order on Motion to Dismiss",
        content="Court grants defendant's motion to dismiss for lack of jurisdiction.",
        content_hash="abc123",
        date_filed=datetime(2026, 4, 7, tzinfo=timezone.utc),
        embedded=False,
    )


def test_get_index_creates_index_directory(tmp_index_path: Path):
    index = get_index()
    assert tmp_index_path.exists()


def test_upsert_entry_indexes_without_error(tmp_index_path: Path, sample_entry: DocketEntry):
    index = get_index()
    upsert_entry(index, sample_entry)  # should not raise


def test_upsert_entry_is_idempotent(tmp_index_path: Path, sample_entry: DocketEntry):
    index = get_index()
    upsert_entry(index, sample_entry)
    upsert_entry(index, sample_entry)  # second upsert must not raise or duplicate
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/ingestion/test_indexer.py -v
```

Expected: `ImportError: cannot import name 'get_index'`

- [ ] **Step 3: Create `docketmind/ingestion/indexer.py`**

```python
"""LlamaIndex vector store: index creation, document upsert."""

from pathlib import Path

from llama_index.core import (
    Document,
    Settings as LlamaConfig,
    StorageContext,
    VectorStoreIndex,
    load_index_from_storage,
)
from llama_index.core.readers import PDFReader
from llama_index.embeddings.openai import OpenAIEmbedding

from docketmind.config import settings
from docketmind.models import DocketEntry, DocketEntryDocument


def _configure_llama() -> None:
    """Set the OpenAI embedding model on the LlamaIndex global settings."""
    LlamaConfig.embed_model = OpenAIEmbedding(
        model=settings.openai_embedding_model,
        api_key=settings.openai_api_key,
    )


def get_index() -> VectorStoreIndex:
    """Load the persisted vector index from disk, or create a new empty one."""
    _configure_llama()
    index_path = settings.index_path

    if index_path.exists() and any(index_path.iterdir()):
        storage_context = StorageContext.from_defaults(persist_dir=str(index_path))
        return load_index_from_storage(storage_context)

    index_path.mkdir(parents=True, exist_ok=True)
    index = VectorStoreIndex([], storage_context=StorageContext.from_defaults())
    index.storage_context.persist(persist_dir=str(index_path))
    return index


def _save(index: VectorStoreIndex) -> None:
    """Persist the index to disk."""
    index.storage_context.persist(persist_dir=str(settings.index_path))


def upsert_entry(index: VectorStoreIndex, entry: DocketEntry) -> None:
    """Index a docket entry's text into the vector store.

    Uses entry.id as the document ID so re-indexing the same entry
    replaces the old version rather than creating a duplicate.
    """
    doc = Document(
        text=f"{entry.title}\n\n{entry.content}",
        doc_id=str(entry.id),
        metadata={
            "case_id": str(entry.case_id),
            "court_listener_id": entry.court_listener_id,
            "date_filed": entry.date_filed.isoformat(),
            "type": "docket_entry",
        },
    )
    try:
        index.delete_ref_doc(str(entry.id), delete_from_docstore=True)
    except Exception:
        pass
    index.insert(doc)
    _save(index)


def upsert_document(
    index: VectorStoreIndex, doc_model: DocketEntryDocument, pdf_path: Path
) -> None:
    """Index all pages of a PDF document into the vector store.

    Each page becomes a separate LlamaIndex Document keyed by
    `<doc_model.id>_page_<n>` for idempotent upserts.
    """
    reader = PDFReader()
    pages = reader.load_data(file=pdf_path)

    for i, page in enumerate(pages):
        doc_id = f"{doc_model.id}_page_{i}"
        page.doc_id = doc_id
        page.metadata.update(
            {
                "docket_entry_id": str(doc_model.docket_entry_id),
                "pdf_url": doc_model.pdf_url,
                "type": "pdf_document",
            }
        )
        try:
            index.delete_ref_doc(doc_id, delete_from_docstore=True)
        except Exception:
            pass
        index.insert(page)

    _save(index)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/ingestion/test_indexer.py -v
```

Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add docketmind/ingestion/indexer.py tests/ingestion/test_indexer.py
git commit -m "feat: add LlamaIndex vector store indexer"
```

---

## Task 6: Memory Updater

**Files:**
- Create: `docketmind/ingestion/memory.py`
- Create: `tests/ingestion/test_memory.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/ingestion/test_memory.py`:

```python
"""Tests for per-case memory updater."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from docketmind.ingestion.memory import update_case_memory
from docketmind.models import Case, DocketEntry


@pytest.fixture
def case_no_memory() -> Case:
    return Case(
        id="case-001",
        court_listener_id="12345",
        name="United States v. Doe",
        court="D. Mass.",
        memory_text=None,
    )


@pytest.fixture
def case_with_memory() -> Case:
    return Case(
        id="case-001",
        court_listener_id="12345",
        name="United States v. Doe",
        court="D. Mass.",
        memory_text="Prior summary: case involves tax fraud allegations.",
    )


@pytest.fixture
def new_entries() -> list[DocketEntry]:
    return [
        DocketEntry(
            id="entry-001",
            case_id="case-001",
            court_listener_id="cl-001",
            title="Order GRANTING Motion to Dismiss",
            content="Court grants motion to dismiss counts 1-3.",
            content_hash="abc",
            date_filed=datetime(2026, 4, 7, tzinfo=timezone.utc),
        )
    ]


async def test_update_case_memory_returns_string(case_no_memory, new_entries):
    mock_response = MagicMock()
    mock_response.choices[0].message.content = "Updated summary text."

    with patch("docketmind.ingestion.memory._client") as mock_client:
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        result = await update_case_memory(case_no_memory, new_entries)

    assert result == "Updated summary text."


async def test_update_case_memory_includes_existing_memory_in_prompt(
    case_with_memory, new_entries
):
    mock_response = MagicMock()
    mock_response.choices[0].message.content = "New summary."

    with patch("docketmind.ingestion.memory._client") as mock_client:
        create_mock = AsyncMock(return_value=mock_response)
        mock_client.chat.completions.create = create_mock
        await update_case_memory(case_with_memory, new_entries)

    prompt = create_mock.call_args[1]["messages"][0]["content"]
    assert "Prior summary: case involves tax fraud allegations." in prompt


async def test_update_case_memory_includes_new_entries_in_prompt(
    case_no_memory, new_entries
):
    mock_response = MagicMock()
    mock_response.choices[0].message.content = "New summary."

    with patch("docketmind.ingestion.memory._client") as mock_client:
        create_mock = AsyncMock(return_value=mock_response)
        mock_client.chat.completions.create = create_mock
        await update_case_memory(case_no_memory, new_entries)

    prompt = create_mock.call_args[1]["messages"][0]["content"]
    assert "Order GRANTING Motion to Dismiss" in prompt
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/ingestion/test_memory.py -v
```

Expected: `ImportError: cannot import name 'update_case_memory'`

- [ ] **Step 3: Create `docketmind/ingestion/memory.py`**

```python
"""Per-case memory updater: summarizes new docket entries via OpenAI."""

from openai import AsyncOpenAI

from docketmind.config import settings
from docketmind.models import Case, DocketEntry

_client = AsyncOpenAI(api_key=settings.openai_api_key)


async def update_case_memory(case: Case, new_entries: list[DocketEntry]) -> str:
    """Generate an updated memory summary for a case given new docket entries.

    Passes the existing memory (if any) and the new entries to the LLM
    and returns the updated summary text to be stored in Case.memory_text.
    """
    entries_text = "\n\n".join(
        f"[{e.date_filed.strftime('%Y-%m-%d')}] {e.title}\n{e.content}"
        for e in new_entries
    )

    current_summary = case.memory_text or "No summary yet — this is the first batch of entries."

    prompt = (
        "You are a legal analyst tracking federal lawsuits. "
        "Update the case summary below with the new docket entries provided. "
        "Cover: current posture, recent key filings, notable rulings, "
        "upcoming deadlines, and major parties/arguments.\n\n"
        f"Current summary:\n{current_summary}\n\n"
        f"New docket entries:\n{entries_text}\n\n"
        "Write a concise updated summary (2-4 paragraphs)."
    )

    response = await _client.chat.completions.create(
        model=settings.openai_llm_model,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content or ""
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/ingestion/test_memory.py -v
```

Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add docketmind/ingestion/memory.py tests/ingestion/test_memory.py
git commit -m "feat: add per-case memory updater"
```

---

## Task 7: Pipeline Orchestrator

**Files:**
- Create: `docketmind/ingestion/pipeline.py`
- Create: `tests/ingestion/test_pipeline.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/ingestion/test_pipeline.py`:

```python
"""Tests for the case sync pipeline."""

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import docketmind.db as db_module
from docketmind.ingestion.pipeline import SyncResult, sync_case
from docketmind.ingestion.rss import RawEntry
from docketmind.models import Base, Case


@pytest.fixture(autouse=True)
async def in_memory_db():
    """Wire up an in-memory SQLite DB for each test."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    db_module.engine = engine
    db_module.async_session = async_sessionmaker(engine, expire_on_commit=False)
    yield
    await engine.dispose()


@pytest.fixture
async def saved_case() -> Case:
    """Insert a Case into the in-memory DB and return it."""
    async with db_module.async_session() as session:
        case = Case(
            id="case-001",
            court_listener_id="12345",
            name="United States v. Doe",
            court="D. Mass.",
        )
        session.add(case)
        await session.commit()
    return case


@pytest.fixture
def raw_entry_no_pdf() -> RawEntry:
    return RawEntry(
        court_listener_id="cl-001",
        title="Order on Motion",
        content="Court rules on motion.",
        content_hash="hash-001",
        date_filed=datetime(2026, 4, 7, tzinfo=timezone.utc),
        pdf_urls=[],
    )


@pytest.fixture
def raw_entry_with_pdf() -> RawEntry:
    return RawEntry(
        court_listener_id="cl-002",
        title="Filed Motion",
        content="Defendant files motion.",
        content_hash="hash-002",
        date_filed=datetime(2026, 4, 8, tzinfo=timezone.utc),
        pdf_urls=["https://storage.courtlistener.com/recap/doc.pdf"],
    )


async def test_sync_case_returns_sync_result_for_unknown_case():
    result = await sync_case("nonexistent-id")
    assert isinstance(result, SyncResult)
    assert result.errors


async def test_sync_case_inserts_new_entries(saved_case, raw_entry_no_pdf):
    with (
        patch("docketmind.ingestion.pipeline.fetch_feed", AsyncMock(return_value=[raw_entry_no_pdf])),
        patch("docketmind.ingestion.pipeline.get_index", MagicMock(return_value=MagicMock())),
        patch("docketmind.ingestion.pipeline.upsert_entry"),
        patch("docketmind.ingestion.pipeline.update_case_memory", AsyncMock(return_value="summary")),
    ):
        result = await sync_case("case-001")

    assert result.new_entries == 1
    assert result.updated_entries == 0


async def test_sync_case_detects_changed_entry(saved_case, raw_entry_no_pdf):
    # First sync: insert the entry
    with (
        patch("docketmind.ingestion.pipeline.fetch_feed", AsyncMock(return_value=[raw_entry_no_pdf])),
        patch("docketmind.ingestion.pipeline.get_index", MagicMock(return_value=MagicMock())),
        patch("docketmind.ingestion.pipeline.upsert_entry"),
        patch("docketmind.ingestion.pipeline.update_case_memory", AsyncMock(return_value="summary")),
    ):
        await sync_case("case-001")

    # Second sync: same entry but different hash
    changed_entry = raw_entry_no_pdf.model_copy(update={"content_hash": "new-hash"})
    with (
        patch("docketmind.ingestion.pipeline.fetch_feed", AsyncMock(return_value=[changed_entry])),
        patch("docketmind.ingestion.pipeline.get_index", MagicMock(return_value=MagicMock())),
        patch("docketmind.ingestion.pipeline.upsert_entry"),
        patch("docketmind.ingestion.pipeline.update_case_memory", AsyncMock(return_value="summary")),
    ):
        result = await sync_case("case-001")

    assert result.updated_entries == 1
    assert result.new_entries == 0


async def test_sync_case_is_idempotent(saved_case, raw_entry_no_pdf):
    """Running sync twice with unchanged entries produces zero new/updated on second run."""
    patches = {
        "fetch": AsyncMock(return_value=[raw_entry_no_pdf]),
        "index": MagicMock(return_value=MagicMock()),
    }
    with (
        patch("docketmind.ingestion.pipeline.fetch_feed", patches["fetch"]),
        patch("docketmind.ingestion.pipeline.get_index", patches["index"]),
        patch("docketmind.ingestion.pipeline.upsert_entry"),
        patch("docketmind.ingestion.pipeline.update_case_memory", AsyncMock(return_value="s")),
    ):
        await sync_case("case-001")
        result = await sync_case("case-001")

    assert result.new_entries == 0
    assert result.updated_entries == 0
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/ingestion/test_pipeline.py -v
```

Expected: `ImportError: cannot import name 'sync_case'`

- [ ] **Step 3: Create `docketmind/ingestion/pipeline.py`**

```python
"""Case sync pipeline: reconcile docket entries, download PDFs, embed, update memory."""

from datetime import datetime, timezone
from pathlib import Path

from loguru import logger
from pydantic import BaseModel
from sqlalchemy import select

import docketmind.db as db_module
from docketmind.config import settings
from docketmind.ingestion.downloader import download_pdf
from docketmind.ingestion.indexer import get_index, upsert_document, upsert_entry
from docketmind.ingestion.memory import update_case_memory
from docketmind.ingestion.rss import fetch_feed
from docketmind.models import Case, DocketEntry, DocketEntryDocument


class SyncResult(BaseModel):
    """Result of a single case sync operation."""

    case_id: str
    new_entries: int = 0
    updated_entries: int = 0
    new_documents: int = 0
    downloaded_documents: int = 0
    memory_updated: bool = False
    errors: list[str] = []


async def sync_case(case_id: str) -> SyncResult:
    """Run a full reconciliation sync for a single case.

    Fetches the complete CourtListener RSS feed, reconciles entries and
    documents against SQLite, downloads new PDFs, embeds new/changed
    content, and updates the case memory if anything changed.
    """
    result = SyncResult(case_id=case_id)

    async with db_module.async_session() as session:
        case = await session.get(Case, case_id)
        if case is None:
            result.errors.append(f"Case {case_id} not found in database")
            return result

        # Step 1: Fetch full RSS feed
        try:
            raw_entries = await fetch_feed(case.rss_url)
        except Exception as exc:
            result.errors.append(f"RSS fetch failed: {exc}")
            logger.error(f"RSS fetch failed for case {case_id}: {exc}")
            return result

        # Step 2: Load existing entries
        rows = await session.execute(
            select(DocketEntry).where(DocketEntry.case_id == case_id)
        )
        existing_by_cl_id: dict[str, DocketEntry] = {
            e.court_listener_id: e for e in rows.scalars()
        }

        # Step 3+4: Reconcile entries and documents
        changed_entries: list[DocketEntry] = []

        for raw in raw_entries:
            existing = existing_by_cl_id.get(raw.court_listener_id)

            if existing is None:
                entry = DocketEntry(
                    case_id=case_id,
                    court_listener_id=raw.court_listener_id,
                    title=raw.title,
                    content=raw.content,
                    content_hash=raw.content_hash,
                    date_filed=raw.date_filed,
                    embedded=False,
                )
                session.add(entry)
                await session.flush()
                result.new_entries += 1
                changed_entries.append(entry)
            elif existing.content_hash != raw.content_hash:
                existing.title = raw.title
                existing.content = raw.content
                existing.content_hash = raw.content_hash
                existing.embedded = False
                existing.updated_at = datetime.now(timezone.utc)
                result.updated_entries += 1
                changed_entries.append(existing)
                entry = existing
            else:
                entry = existing

            # Reconcile PDF documents for this entry
            doc_rows = await session.execute(
                select(DocketEntryDocument).where(
                    DocketEntryDocument.docket_entry_id == entry.id
                )
            )
            existing_urls = {d.pdf_url for d in doc_rows.scalars()}

            for pdf_url in raw.pdf_urls:
                if pdf_url not in existing_urls:
                    doc = DocketEntryDocument(
                        docket_entry_id=entry.id,
                        pdf_url=pdf_url,
                        downloaded=False,
                        embedded=False,
                    )
                    session.add(doc)
                    result.new_documents += 1

        await session.commit()

        # Step 4b: Download pending PDFs
        pending = await session.execute(
            select(DocketEntryDocument)
            .join(DocketEntry)
            .where(DocketEntry.case_id == case_id)
            .where(DocketEntryDocument.downloaded == False)  # noqa: E712
        )
        for doc in pending.scalars():
            filename = doc.pdf_url.rstrip("/").split("/")[-1]
            dest = settings.pdfs_path / case_id / filename
            try:
                await download_pdf(doc.pdf_url, dest)
                doc.pdf_path = str(dest)
                doc.downloaded = True
                result.downloaded_documents += 1
            except Exception as exc:
                result.errors.append(f"PDF download failed {doc.pdf_url}: {exc}")
                logger.warning(f"PDF download failed for case {case_id}: {exc}")

        await session.commit()

        # Step 5: Embed unembedded entries
        index = get_index()

        unembedded_entries = await session.execute(
            select(DocketEntry)
            .where(DocketEntry.case_id == case_id)
            .where(DocketEntry.embedded == False)  # noqa: E712
        )
        for entry in unembedded_entries.scalars():
            try:
                upsert_entry(index, entry)
                entry.embedded = True
            except Exception as exc:
                result.errors.append(f"Embed failed for entry {entry.id}: {exc}")
                logger.error(f"Embed failed for entry {entry.id}: {exc}")

        # Step 5b: Embed downloaded PDFs
        unembedded_docs = await session.execute(
            select(DocketEntryDocument)
            .join(DocketEntry)
            .where(DocketEntry.case_id == case_id)
            .where(DocketEntryDocument.downloaded == True)  # noqa: E712
            .where(DocketEntryDocument.embedded == False)   # noqa: E712
        )
        for doc in unembedded_docs.scalars():
            if doc.pdf_path:
                try:
                    upsert_document(index, doc, Path(doc.pdf_path))
                    doc.embedded = True
                except Exception as exc:
                    result.errors.append(f"Embed failed for doc {doc.id}: {exc}")
                    logger.error(f"Embed failed for doc {doc.id}: {exc}")

        await session.commit()

        # Step 6: Update memory if anything changed
        if changed_entries:
            try:
                new_memory = await update_case_memory(case, changed_entries)
                case.memory_text = new_memory
                result.memory_updated = True
            except Exception as exc:
                result.errors.append(f"Memory update failed: {exc}")
                logger.error(f"Memory update failed for case {case_id}: {exc}")

        # Step 7: Mark last synced
        case.last_synced_at = datetime.now(timezone.utc)
        await session.commit()

    logger.info(
        f"Sync complete case={case_id} "
        f"new={result.new_entries} updated={result.updated_entries} "
        f"pdfs={result.downloaded_documents} errors={len(result.errors)}"
    )
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/ingestion/test_pipeline.py -v
```

Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add docketmind/ingestion/pipeline.py tests/ingestion/test_pipeline.py
git commit -m "feat: add case sync pipeline orchestrator"
```

---

## Task 8: Scheduler

**Files:**
- Create: `docketmind/ingestion/scheduler.py`
- Create: `tests/ingestion/test_scheduler.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/ingestion/test_scheduler.py`:

```python
"""Tests for APScheduler per-case job registration."""

from unittest.mock import AsyncMock, patch

import pytest

from docketmind.ingestion.scheduler import add_case, remove_case, _scheduler


@pytest.fixture(autouse=True)
def fresh_scheduler():
    """Ensure scheduler is stopped and jobs are cleared between tests."""
    if _scheduler.running:
        _scheduler.shutdown(wait=False)
    _scheduler.remove_all_jobs()
    _scheduler.start()
    yield
    if _scheduler.running:
        _scheduler.shutdown(wait=False)
    _scheduler.remove_all_jobs()


async def test_add_case_registers_interval_job():
    with patch("docketmind.ingestion.scheduler._run_sync", AsyncMock()):
        await add_case("case-001")

    job = _scheduler.get_job("sync_case-001")
    assert job is not None


async def test_add_case_triggers_immediate_sync():
    run_sync = AsyncMock()
    with patch("docketmind.ingestion.scheduler._run_sync", run_sync):
        await add_case("case-001")

    run_sync.assert_awaited_once_with("case-001")


async def test_remove_case_removes_job():
    with patch("docketmind.ingestion.scheduler._run_sync", AsyncMock()):
        await add_case("case-001")

    remove_case("case-001")

    assert _scheduler.get_job("sync_case-001") is None


async def test_remove_case_is_safe_when_job_does_not_exist():
    remove_case("nonexistent-case")  # must not raise
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/ingestion/test_scheduler.py -v
```

Expected: `ImportError: cannot import name 'add_case'`

- [ ] **Step 3: Create `docketmind/ingestion/scheduler.py`**

```python
"""APScheduler configuration for per-case RSS polling jobs."""

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from loguru import logger
from sqlalchemy import select

import docketmind.db as db_module
from docketmind.config import settings
from docketmind.ingestion.pipeline import sync_case
from docketmind.models import Case

_scheduler = AsyncIOScheduler()


async def _run_sync(case_id: str) -> None:
    """Run sync_case and log any unhandled errors."""
    try:
        result = await sync_case(case_id)
        if result.errors:
            logger.warning(f"Sync for case {case_id} completed with errors: {result.errors}")
    except Exception as exc:
        logger.error(f"Unhandled error syncing case {case_id}: {exc}")


def _register_job(case_id: str) -> None:
    """Register an interval polling job for a case."""
    _scheduler.add_job(
        _run_sync,
        "interval",
        seconds=settings.poll_interval_seconds,
        args=[case_id],
        id=f"sync_{case_id}",
        replace_existing=True,
    )


async def start() -> None:
    """Start the scheduler and re-register all existing cases from the database."""
    async with db_module.async_session() as session:
        rows = await session.execute(select(Case))
        cases = rows.scalars().all()

    for case in cases:
        _register_job(case.id)

    _scheduler.start()
    logger.info(f"Scheduler started, registered {len(cases)} case(s)")


async def add_case(case_id: str) -> None:
    """Register a polling job for a new case and trigger an immediate backfill."""
    _register_job(case_id)
    await _run_sync(case_id)
    logger.info(f"Added case {case_id} to scheduler and triggered backfill")


def remove_case(case_id: str) -> None:
    """Remove the polling job for a deleted case."""
    job_id = f"sync_{case_id}"
    if _scheduler.get_job(job_id):
        _scheduler.remove_job(job_id)
        logger.info(f"Removed scheduler job for case {case_id}")
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/ingestion/test_scheduler.py -v
```

Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add docketmind/ingestion/scheduler.py tests/ingestion/test_scheduler.py
git commit -m "feat: add APScheduler per-case polling jobs"
```

---

## Task 9: Public API + Full Suite

**Files:**
- Modify: `docketmind/ingestion/__init__.py`

- [ ] **Step 1: Expose public API from package init**

Update `docketmind/ingestion/__init__.py`:

```python
"""Ingestion package: RSS polling, PDF downloading, embedding, and memory updates."""

from docketmind.ingestion.pipeline import SyncResult, sync_case
from docketmind.ingestion.scheduler import add_case, remove_case, start

__all__ = ["sync_case", "SyncResult", "start", "add_case", "remove_case"]
```

- [ ] **Step 2: Run the full test suite**

```bash
uv run pytest tests/ -v --tb=short
```

Expected: all tests pass, 0 failures.

- [ ] **Step 3: Run type checker**

```bash
uv run pyright docketmind/
```

Expected: 0 errors.

- [ ] **Step 4: Run linter**

```bash
uv run ruff check docketmind/ tests/
```

Expected: no issues (or fix any reported).

- [ ] **Step 5: Final commit**

```bash
git add docketmind/ingestion/__init__.py
git commit -m "feat: expose ingestion public API and complete ingestion package"
```
