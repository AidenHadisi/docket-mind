"""Ingest pipeline: RSS fetching, PDF downloading, and case sync."""

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiofiles
import feedparser
import httpx
import stamina
from loguru import logger
from pydantic import BaseModel, Field, computed_field

from docketmind import index
from docketmind import store as db
from docketmind.configure import settings
from docketmind.store import (
    DocketEntry,
    DocketEntryDocument,
    get_case,
    list_documents_for_entry,
    list_entries_for_case,
    list_pending_downloads,
    list_unembedded_documents,
    list_unembedded_entries,
)

_client = httpx.AsyncClient(follow_redirects=True)


class RawEntry(BaseModel):
    """A single docket entry parsed from a CourtListener RSS feed."""

    court_listener_id: str = Field(min_length=1)
    title: str
    content: str
    date_filed: datetime
    pdf_urls: list[str]

    @computed_field
    @property
    def content_hash(self) -> str:
        """SHA-256 of title + content, used for change detection."""
        return hashlib.sha256(f"{self.title}\n{self.content}".encode()).hexdigest()


def _is_cl_pdf(url: str) -> bool:
    """Return True if url is a CourtListener storage PDF."""
    return "storage.courtlistener.com" in url and url.endswith(".pdf")


def _extract_pdf_urls(entry: feedparser.FeedParserDict) -> list[str]:
    """Extract CourtListener storage PDF URLs from entry enclosures and links.

    CourtListener Atom feeds set type="None" on all enclosures, so MIME type
    is not a reliable signal. Instead, URLs are matched by host and extension.
    The enclosure loop handles RSS 2.0; the links loop handles Atom (where
    enclosures appear as <link rel="enclosure">).
    """
    seen: set[str] = set()
    urls: list[str] = []
    enclosures: list[Any] = getattr(entry, "enclosures", [])
    links: list[Any] = getattr(entry, "links", [])

    for item in (*enclosures, *links):
        url = str(item.get("url", "") or item.get("href", ""))
        if _is_cl_pdf(url) and url not in seen:
            seen.add(url)
            urls.append(url)

    return urls


async def fetch_case_metadata(rss_url: str) -> str:
    """Return the case name from a CourtListener RSS feed header.

    Parses feed.feed.title for the case name.
    Returns "Unknown Case" if the feed is empty or the field is absent.

    Raises httpx.HTTPStatusError on non-2xx responses.
    """
    response = await _client.get(rss_url, timeout=30)
    response.raise_for_status()

    feed = feedparser.parse(response.text)
    feed_meta: dict[str, Any] = feed.feed  # type: ignore[assignment]  # stubs incorrectly type feed.feed as list
    return str(feed_meta.get("title", "Unknown Case")) or "Unknown Case"


async def fetch_feed(rss_url: str) -> list[RawEntry]:
    """Fetch and parse a CourtListener RSS feed, returning all docket entries.

    Raises httpx.HTTPStatusError on non-2xx responses.
    """
    response = await _client.get(rss_url, timeout=30)
    response.raise_for_status()

    feed = feedparser.parse(response.text)

    entries: list[RawEntry] = []
    for item in feed.entries:
        content = str(item.get("summary") or "")

        if item.get("published_parsed"):
            # feedparser returns a time.struct_time; first 6 fields are Y-M-D-H-M-S.
            date_filed = datetime(*item.published_parsed[:6], tzinfo=UTC)  # type: ignore[misc]
        else:
            logger.warning(
                "RSS entry missing pubDate, using current time: court_listener_id={!r}",
                item.get("id") or item.get("link", ""),
            )
            date_filed = datetime.now(UTC)

        court_listener_id: str = str(item.get("id") or item.get("link", ""))
        title: str = str(item.get("title", ""))

        entries.append(
            RawEntry(
                court_listener_id=court_listener_id,
                title=title,
                content=content,
                date_filed=date_filed,
                pdf_urls=_extract_pdf_urls(item),
            )
        )

    return entries


@stamina.retry(on=httpx.TransportError, attempts=3)
async def download_pdf(url: str, dest: Path) -> None:
    """Download a PDF from url and stream it to dest.

    Parent directories are created automatically. Retries up to 3 times
    on transient transport errors with exponential backoff via stamina.

    Raises httpx.HTTPStatusError if the server returns a non-2xx status.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)

    async with _client.stream("GET", url, timeout=60) as response:
        response.raise_for_status()
        async with aiofiles.open(dest, "wb") as f:
            async for chunk in response.aiter_bytes(chunk_size=65536):
                await f.write(chunk)


class SyncResult(BaseModel):
    """Result of a single case sync operation."""

    case_id: str
    new_entries: int = 0
    updated_entries: int = 0
    new_documents: int = 0
    downloaded_documents: int = 0
    errors: list[str] = Field(default_factory=list)


async def sync_case(case_id: str) -> SyncResult:
    """Run a full reconciliation sync for a single case.

    Fetches the complete CourtListener RSS feed, reconciles entries and
    documents against SQLite, downloads new PDFs, and embeds new/changed
    content into the vector index via the ingestion pipeline.
    """
    result = SyncResult(case_id=case_id)

    async with db.async_session() as session:
        case = await get_case(session, case_id)
        if case is None:
            result.errors.append(f"Case {case_id} not found in database")
            return result

        try:
            raw_entries = await fetch_feed(case.rss_url)
        except Exception as exc:
            result.errors.append(f"RSS fetch failed: {exc}")
            logger.error("RSS fetch failed for case {}: {}", case_id, exc)
            return result

        existing_entries = await list_entries_for_case(session, case_id)
        # Index by CourtListener ID for O(1) dedup against the feed.
        existing_by_cl_id = {e.court_listener_id: e for e in existing_entries}

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
                # Flush to assign entry.id so we can link documents below.
                await session.flush()
                result.new_entries += 1
            elif existing.content_hash != raw.content_hash:
                # Content changed since last sync; mark for re-embedding.
                existing.title = raw.title
                existing.content = raw.content
                existing.content_hash = raw.content_hash
                existing.embedded = False
                existing.updated_at = datetime.now(UTC)
                result.updated_entries += 1
                entry = existing
            else:
                entry = existing

            existing_docs = await list_documents_for_entry(session, entry.id)
            existing_urls = {d.pdf_url for d in existing_docs}

            for pdf_url in raw.pdf_urls:
                if pdf_url not in existing_urls:
                    session.add(DocketEntryDocument(docket_entry_id=entry.id, pdf_url=pdf_url))
                    result.new_documents += 1

        await session.commit()

        # PDFs are keyed by court_listener_id (not the internal UUID) so the
        # on-disk cache survives remove/re-add cycles.
        for doc in await list_pending_downloads(session, case_id):
            filename = doc.pdf_url.rstrip("/").split("/")[-1]
            dest = settings.pdfs_path / case.court_listener_id / filename
            try:
                if not dest.exists():
                    await download_pdf(doc.pdf_url, dest)
                doc.pdf_path = str(dest)
                doc.downloaded = True
                result.downloaded_documents += 1
            except Exception as exc:
                result.errors.append(f"PDF download failed {doc.pdf_url}: {exc}")
                logger.warning("PDF download failed for case {}: {}", case_id, exc)

        await session.commit()

        for entry in await list_unembedded_entries(session, case_id):
            try:
                await index.upsert_entry(entry)
                entry.embedded = True
            except Exception as exc:
                result.errors.append(f"Embed failed for entry {entry.id}: {exc}")
                logger.error("Embed failed for entry {}: {}", entry.id, exc)

        for doc in await list_unembedded_documents(session, case_id):
            if doc.pdf_path:
                try:
                    await index.upsert_document(
                        doc,
                        Path(doc.pdf_path),
                        date_filed=doc.entry.date_filed.isoformat(),
                        title=doc.entry.title,
                    )
                    doc.embedded = True
                except Exception as exc:
                    result.errors.append(f"Embed failed for doc {doc.id}: {exc}")
                    logger.error("Embed failed for doc {}: {}", doc.id, exc)

        case.last_synced_at = datetime.now(UTC)
        await session.commit()

    logger.info(
        "Sync complete case={} new={} updated={} pdfs={} errors={}",
        case_id,
        result.new_entries,
        result.updated_entries,
        result.downloaded_documents,
        len(result.errors),
    )
    return result
