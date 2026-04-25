"""Tests for PDF downloader."""

from pathlib import Path

import httpx
import pytest

from docketmind.ingest import download_pdf

PDF_URL = "https://storage.courtlistener.com/recap/gov.uscourts.test.001.pdf"
FAKE_PDF = b"%PDF-1.4 fake pdf content"


async def test_download_pdf_writes_file_to_disk(respx_mock, tmp_path: Path):
    dest = tmp_path / "test.pdf"
    respx_mock.get(PDF_URL).mock(return_value=httpx.Response(200, content=FAKE_PDF))

    await download_pdf(PDF_URL, dest)

    assert dest.exists()
    assert dest.read_bytes() == FAKE_PDF


async def test_download_pdf_creates_parent_directories(respx_mock, tmp_path: Path):
    dest = tmp_path / "case-uuid" / "subdir" / "test.pdf"
    respx_mock.get(PDF_URL).mock(return_value=httpx.Response(200, content=FAKE_PDF))

    await download_pdf(PDF_URL, dest)

    assert dest.exists()


async def test_download_pdf_raises_on_http_error(respx_mock, tmp_path: Path):
    dest = tmp_path / "test.pdf"
    respx_mock.get(PDF_URL).mock(return_value=httpx.Response(403))

    with pytest.raises(httpx.HTTPStatusError):
        await download_pdf(PDF_URL, dest)
