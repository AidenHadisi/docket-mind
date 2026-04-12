"""Tests for CourtListener RSS feed fetching and parsing."""

import hashlib
from datetime import UTC, datetime

import httpx
import pytest

from docketmind.ingestion.rss import RawEntry, fetch_feed

FEED_URL = "https://www.courtlistener.com/docket/12345/feed/"

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


RSS_FIXTURE_MISSING_PUB_DATE = """\
<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0">
  <channel>
    <title>United States v. Doe</title>
    <item>
      <title>Minute Entry</title>
      <guid isPermaLink="false">https://www.courtlistener.com/docket/12345/recaps/003</guid>
      <description>&lt;p&gt;No pubDate present.&lt;/p&gt;</description>
    </item>
  </channel>
</rss>
"""


RSS_FIXTURE_MIXED_ENCLOSURES = """\
<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0">
  <channel>
    <title>United States v. Doe</title>
    <item>
      <title>Attachment Review</title>
      <guid isPermaLink="false">https://www.courtlistener.com/docket/12345/recaps/004</guid>
      <pubDate>Tue, 08 Apr 2026 09:00:00 +0000</pubDate>
      <description>&lt;p&gt;Entry with mixed enclosures.&lt;/p&gt;</description>
      <enclosure
        url="https://storage.courtlistener.com/recap/gov.uscourts.test.valid.pdf"
        type="application/pdf"
        length="10000"/>
      <enclosure
        url="https://example.com/external.pdf"
        type="application/pdf"
        length="9999"/>
      <enclosure
        url="https://storage.courtlistener.com/recap/not-a-pdf.txt"
        type="text/plain"
        length="500"/>
    </item>
  </channel>
</rss>
"""


@pytest.fixture
def mock_feed(respx_mock):
    def _mock_feed(*, text: str = RSS_FIXTURE, status_code: int = 200):
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
        (0, "content", "Court grants defendant's motion to dismiss."),
    ],
    ids=["first-entry-pdf-urls", "second-entry-empty-pdf-urls", "html-stripped-content"],
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


async def test_fetch_feed_uses_current_time_when_pub_date_missing(mock_feed):
    mock_feed(text=RSS_FIXTURE_MISSING_PUB_DATE)
    before = datetime.now(UTC)
    entries = await fetch_feed(FEED_URL)
    after = datetime.now(UTC)

    assert len(entries) == 1
    assert entries[0].date_filed.tzinfo == UTC
    assert before <= entries[0].date_filed <= after


async def test_fetch_feed_filters_non_courtlistener_or_non_pdf_enclosures(mock_feed):
    mock_feed(text=RSS_FIXTURE_MIXED_ENCLOSURES)
    entries = await fetch_feed(FEED_URL)

    assert entries[0].pdf_urls == [
        "https://storage.courtlistener.com/recap/gov.uscourts.test.valid.pdf"
    ]
