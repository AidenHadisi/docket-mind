"""CourtListener RSS feed fetcher and parser."""

import hashlib
from datetime import UTC, datetime

import feedparser
import httpx
from loguru import logger
from pydantic import BaseModel, Field


class RawEntry(BaseModel):
    """A single docket entry parsed from a CourtListener RSS feed."""

    court_listener_id: str = Field(min_length=1)
    title: str
    content: str
    content_hash: str
    date_filed: datetime
    pdf_urls: list[str]


def _compute_hash(title: str, content: str) -> str:
    """Compute a SHA-256 hash of title + content for change detection."""
    return hashlib.sha256(f"{title}\n{content}".encode()).hexdigest()


def _extract_pdf_urls(entry: feedparser.FeedParserDict) -> list[str]:
    """Extract CourtListener storage PDF URLs from entry enclosures and links.

    CourtListener Atom feeds set type="None" on all enclosures, so MIME type
    is not a reliable signal. Instead, URLs are matched by host and extension.
    The enclosure loop handles RSS 2.0; the links loop handles Atom (where
    enclosures appear as <link rel="enclosure">).
    """
    urls: list[str] = []

    for enclosure in getattr(entry, "enclosures", []):
        url = enclosure.get("url", "")
        if "storage.courtlistener.com" in url and url.endswith(".pdf"):
            urls.append(url)

    for link in getattr(entry, "links", []):
        url = link.get("href", "")
        if "storage.courtlistener.com" in url and url.endswith(".pdf") and url not in urls:
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
        content = getattr(item, "summary", "") or ""

        if item.get("published_parsed"):
            raw_t: tuple[object, ...] = tuple(item.published_parsed)
            t: tuple[int, ...] = tuple(int(v) for v in raw_t)  # type: ignore[arg-type]
            date_filed = datetime(t[0], t[1], t[2], t[3], t[4], t[5], tzinfo=UTC)
        else:
            logger.warning(
                f"RSS entry missing pubDate, using current time: "
                f"court_listener_id={item.get('id') or item.get('link', '')!r}"
            )
            date_filed = datetime.now(UTC)

        court_listener_id: str = str(item.get("id") or item.get("link", ""))
        title: str = str(item.get("title", ""))

        entries.append(
            RawEntry(
                court_listener_id=court_listener_id,
                title=title,
                content=content,
                content_hash=_compute_hash(title, content),
                date_filed=date_filed,
                pdf_urls=_extract_pdf_urls(item),
            )
        )

    return entries
