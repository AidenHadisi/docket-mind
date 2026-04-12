"""PDF downloader with stamina retry for transient failures."""

from pathlib import Path

import aiofiles
import httpx
import stamina


@stamina.retry(on=httpx.TransportError, attempts=3)
async def download_pdf(url: str, dest: Path) -> None:
    """Download a PDF from url and write it to dest.

    Parent directories are created automatically. Retries up to 5 times
    on transient transport errors (timeouts, connection resets) with
    exponential backoff via stamina.

    Raises httpx.HTTPStatusError if the server returns a non-2xx status.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)

    async with httpx.AsyncClient() as client:
        response = await client.get(url, follow_redirects=True, timeout=60)
        response.raise_for_status()
        async with aiofiles.open(dest, "wb") as f:
            await f.write(response.content)
