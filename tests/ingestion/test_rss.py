"""Tests for CourtListener RSS feed fetching and parsing."""

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
    import hashlib

    respx.get("https://www.courtlistener.com/docket/12345/feed/").mock(
        return_value=httpx.Response(200, text=RSS_FIXTURE)
    )

    entries = await fetch_feed("https://www.courtlistener.com/docket/12345/feed/")

    expected_content = "Court grants defendant's motion to dismiss."
    expected_hash = hashlib.sha256(
        f"Order GRANTING Motion to Dismiss\n{expected_content}".encode()
    ).hexdigest()
    assert entries[0].content_hash == expected_hash


@respx.mock
async def test_fetch_feed_raises_on_http_error():
    respx.get("https://www.courtlistener.com/docket/12345/feed/").mock(
        return_value=httpx.Response(503)
    )

    with pytest.raises(httpx.HTTPStatusError):
        await fetch_feed("https://www.courtlistener.com/docket/12345/feed/")


RSS_NO_PUBDATE = """\
<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0">
  <channel>
    <title>United States v. Doe</title>
    <item>
      <title>Notice of Filing</title>
      <guid isPermaLink="false">https://www.courtlistener.com/docket/99999/recaps/001</guid>
      <description>&lt;p&gt;Attorney files notice.&lt;/p&gt;</description>
    </item>
  </channel>
</rss>
"""


@respx.mock
async def test_fetch_feed_falls_back_when_pubdate_missing():
    """fetch_feed should return a valid RawEntry with a tz-aware date when pubDate is absent."""
    respx.get("https://www.courtlistener.com/docket/99999/feed/").mock(
        return_value=httpx.Response(200, text=RSS_NO_PUBDATE)
    )

    entries = await fetch_feed("https://www.courtlistener.com/docket/99999/feed/")

    assert len(entries) == 1
    assert entries[0].date_filed is not None
    assert entries[0].date_filed.tzinfo is not None  # must be timezone-aware
