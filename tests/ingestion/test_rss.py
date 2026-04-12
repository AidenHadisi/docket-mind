"""Tests for CourtListener RSS feed fetching and parsing."""

import hashlib
from datetime import UTC, datetime

import httpx
import pytest

from docketmind.ingestion.rss import RawEntry, fetch_feed

FEED_URL = "https://www.courtlistener.com/docket/12345/feed/"

# Real CourtListener feeds are Atom, not RSS 2.0. Enclosures use
# <link rel="enclosure" type="None"> — MIME type is never "application/pdf".
ATOM_FIXTURE = """\
<?xml version="1.0" encoding="utf-8"?>
<feed xml:lang="en-us" xmlns="http://www.w3.org/2005/Atom">
  <title>United States v. Doe</title>
  <link href="https://www.courtlistener.com/" rel="alternate"/>
  <link href="https://www.courtlistener.com/docket/12345/feed/" rel="self"/>
  <id>https://www.courtlistener.com/</id>
  <entry>
    <title>Order GRANTING Motion to Dismiss</title>
    <link
      href="https://www.courtlistener.com/docket/12345/doe-v-doe/?order_by=desc#entry-1"
      rel="alternate"/>
    <published>2026-04-07T00:00:00-07:00</published>
    <id>https://www.courtlistener.com/docket/12345/doe-v-doe/?order_by=desc#entry-1</id>
    <summary type="html">Court grants defendant's motion to dismiss.</summary>
    <link
      href="https://storage.courtlistener.com/recap/gov.uscourts.test.001.pdf"
      length="0" rel="enclosure" type="None"/>
  </entry>
  <entry>
    <title>Notice of Appearance</title>
    <link
      href="https://www.courtlistener.com/docket/12345/doe-v-doe/?order_by=desc#entry-2"
      rel="alternate"/>
    <published>2026-04-08T00:00:00-07:00</published>
    <id>https://www.courtlistener.com/docket/12345/doe-v-doe/?order_by=desc#entry-2</id>
    <summary type="html">Attorney files notice of appearance.</summary>
  </entry>
</feed>
"""

# Atom entry with no <published> element — feedparser returns None for published_parsed.
ATOM_FIXTURE_MISSING_PUBLISHED = """\
<?xml version="1.0" encoding="utf-8"?>
<feed xml:lang="en-us" xmlns="http://www.w3.org/2005/Atom">
  <title>United States v. Doe</title>
  <entry>
    <title>Minute Entry</title>
    <link
      href="https://www.courtlistener.com/docket/12345/doe-v-doe/?order_by=desc#minute-entry-001"
      rel="alternate"/>
    <id>https://www.courtlistener.com/docket/12345/doe-v-doe/?order_by=desc#minute-entry-001</id>
    <summary type="html">No published date present.</summary>
  </entry>
</feed>
"""

# One valid storage.courtlistener.com PDF, one ECF link (wrong host),
# one storage.courtlistener.com non-PDF — all with type="None" as in production.
ATOM_FIXTURE_MIXED_ENCLOSURES = """\
<?xml version="1.0" encoding="utf-8"?>
<feed xml:lang="en-us" xmlns="http://www.w3.org/2005/Atom">
  <title>United States v. Doe</title>
  <entry>
    <title>Attachment Review</title>
    <link
      href="https://www.courtlistener.com/docket/12345/doe-v-doe/?order_by=desc#entry-4"
      rel="alternate"/>
    <published>2026-04-08T00:00:00-07:00</published>
    <id>https://www.courtlistener.com/docket/12345/doe-v-doe/?order_by=desc#entry-4</id>
    <summary type="html">Entry with mixed enclosures.</summary>
    <link
      href="https://storage.courtlistener.com/recap/gov.uscourts.test.valid.pdf"
      length="0" rel="enclosure" type="None"/>
    <link
      href="https://ecf.mad.uscourts.gov/doc1/095013569421?caseid=293693"
      length="0" rel="enclosure" type="None"/>
    <link
      href="https://storage.courtlistener.com/recap/not-a-pdf.txt"
      length="0" rel="enclosure" type="None"/>
  </entry>
</feed>
"""


@pytest.fixture
def mock_feed(respx_mock):
    def _mock_feed(*, text: str = ATOM_FIXTURE, status_code: int = 200):
        return respx_mock.get(FEED_URL).mock(return_value=httpx.Response(status_code, text=text))

    return _mock_feed


async def test_fetch_feed_returns_parsed_entries(mock_feed):
    mock_feed()
    entries = await fetch_feed(FEED_URL)

    assert len(entries) == 2
    assert all(isinstance(entry, RawEntry) for entry in entries)


@pytest.mark.parametrize(
    ("entry_index", "field_name", "expected"),
    [
        (
            0,
            "pdf_urls",
            ["https://storage.courtlistener.com/recap/gov.uscourts.test.001.pdf"],
        ),
        (1, "pdf_urls", []),
    ],
    ids=["first-entry-pdf-urls", "second-entry-empty-pdf-urls"],
)
async def test_fetch_feed_extracts_expected_fields(
    mock_feed, entry_index: int, field_name: str, expected
):
    mock_feed()
    entries = await fetch_feed(FEED_URL)

    assert getattr(entries[entry_index], field_name) == expected


async def test_fetch_feed_computes_content_hash(mock_feed):
    mock_feed()
    entries = await fetch_feed(FEED_URL)

    expected_content = "Court grants defendant's motion to dismiss."
    expected_hash = hashlib.sha256(
        f"Order GRANTING Motion to Dismiss\n{expected_content}".encode()
    ).hexdigest()

    assert entries[0].content_hash == expected_hash


async def test_fetch_feed_raises_on_http_error(mock_feed):
    mock_feed(status_code=503, text="")

    with pytest.raises(httpx.HTTPStatusError):
        await fetch_feed(FEED_URL)


async def test_fetch_feed_uses_current_time_when_published_missing(mock_feed):
    mock_feed(text=ATOM_FIXTURE_MISSING_PUBLISHED)
    before = datetime.now(UTC)
    entries = await fetch_feed(FEED_URL)
    after = datetime.now(UTC)

    assert len(entries) == 1
    assert entries[0].date_filed.tzinfo == UTC
    assert before <= entries[0].date_filed <= after


async def test_fetch_feed_filters_non_storage_and_non_pdf_enclosures(mock_feed):
    mock_feed(text=ATOM_FIXTURE_MIXED_ENCLOSURES)
    entries = await fetch_feed(FEED_URL)

    assert entries[0].pdf_urls == [
        "https://storage.courtlistener.com/recap/gov.uscourts.test.valid.pdf"
    ]
