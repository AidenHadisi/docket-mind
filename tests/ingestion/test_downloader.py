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
    route = respx.get(PDF_URL).mock(return_value=httpx.Response(403))

    with pytest.raises(httpx.HTTPStatusError):
        await download_pdf(PDF_URL, dest)

    assert route.call_count == 1
