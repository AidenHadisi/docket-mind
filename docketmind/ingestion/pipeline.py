"""Case sync pipeline: reconcile docket entries, download PDFs, embed, update memory."""

from datetime import UTC, datetime
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
        rows = await session.execute(select(DocketEntry).where(DocketEntry.case_id == case_id))
        existing_by_cl_id: dict[str, DocketEntry] = {e.court_listener_id: e for e in rows.scalars()}

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
                existing.updated_at = datetime.now(UTC)
                result.updated_entries += 1
                changed_entries.append(existing)
                entry = existing
            else:
                entry = existing

            # Reconcile PDF documents for this entry
            doc_rows = await session.execute(
                select(DocketEntryDocument).where(DocketEntryDocument.docket_entry_id == entry.id)
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
            .where(DocketEntryDocument.embedded == False)  # noqa: E712
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
        case.last_synced_at = datetime.now(UTC)
        await session.commit()

    logger.info(
        f"Sync complete case={case_id} "
        f"new={result.new_entries} updated={result.updated_entries} "
        f"pdfs={result.downloaded_documents} errors={len(result.errors)}"
    )
    return result
