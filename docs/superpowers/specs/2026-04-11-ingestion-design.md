# Ingestion Package Design

**Date:** 2026-04-11
**Package:** `docketmind/ingestion/`

---

## Overview

The ingestion package is responsible for everything that brings data into DocketMind: polling CourtListener RSS feeds, reconciling docket entries, downloading PDFs, embedding content into the vector store, and updating per-case memory summaries. It runs as a background process driven by APScheduler and is completely independent of the `bot/` and `intelligence/` packages.

---

## Package Structure

```
docketmind/
├── models.py              # Shared SQLAlchemy models — imported by both ingestion and intelligence
├── ingestion/
│   ├── __init__.py
│   ├── rss.py             # Fetch and parse CourtListener RSS feed → list[RawEntry]
│   ├── downloader.py      # Download PDFs via httpx + aiofiles, retried with stamina
│   ├── indexer.py         # LlamaIndex: load documents, chunk, embed, upsert into vector store
│   ├── memory.py          # Regenerate per-case memory summary via OpenAI after each sync batch
│   ├── pipeline.py        # Orchestrates one full case sync: reconcile → download → embed → memory
│   └── scheduler.py       # APScheduler setup: registers/removes per-case interval jobs
```

`models.py` lives at the top level of `docketmind/` so both `ingestion/` and `intelligence/` can import it without creating a dependency between them.

---

## Data Models (`docketmind/models.py`)

### `Case`

| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID | Primary key |
| `court_listener_id` | str | CourtListener case ID, unique |
| `name` | str | Human-readable case name |
| `court` | str | e.g. "D. Mass." |
| `memory_text` | str (nullable) | LLM-generated summary, overwritten after each sync |
| `last_synced_at` | datetime (nullable) | Timestamp of last successful poll |
| `created_at` | datetime | When the case was added to the bot |

`rss_url` is a computed property — not stored:
```python
@property
def rss_url(self) -> str:
    return f"https://www.courtlistener.com/docket/{self.court_listener_id}/feed/"
```

### `DocketEntry`

| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID | Primary key |
| `case_id` | UUID (FK → Case) | |
| `court_listener_id` | str | Entry ID from CourtListener, unique per case |
| `title` | str | Entry title/description |
| `content_hash` | str | Hash of entry text — used to detect changes on reconciliation |
| `embedded` | bool | Whether this entry has been indexed into the vector store |
| `date_filed` | datetime | When the entry was filed in court |
| `created_at` | datetime | When we first saw this entry |
| `updated_at` | datetime | Last time this entry was modified |

### `DocketEntryDocument`

| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID | Primary key |
| `docket_entry_id` | UUID (FK → DocketEntry) | |
| `pdf_url` | str | CourtListener `.pdf` link |
| `pdf_path` | str (nullable) | Local path once downloaded (`data/pdfs/<case_id>/<filename>`) |
| `downloaded` | bool | Whether the file is on disk |
| `embedded` | bool | Whether this document has been indexed |
| `created_at` | datetime | |

---

## Storage Layout

| Data | Location |
|------|----------|
| Case metadata + memory | SQLite — `Case.memory_text` column |
| Docket entry text + metadata | SQLite — `DocketEntry` table |
| PDF file references + status | SQLite — `DocketEntryDocument` table |
| PDF files | Local disk — `data/pdfs/<case_id>/` |
| Vector embeddings | LlamaIndex SimpleVectorStore — `data/index/` |

---

## Pipeline: `pipeline.py`

`sync_case(case_id)` is the single entrypoint for all ingestion work. It runs on every scheduled poll and on the initial backfill when a case is first added.

```
sync_case(case_id)
    │
    ├── 1. Fetch RSS feed (rss.py)
    │       └── Parse all entries → list[RawEntry]
    │
    ├── 2. Load all existing DocketEntry rows for this case from DB
    │
    ├── 3. Reconcile entries
    │       ├── New entry (court_listener_id not in DB) → insert DocketEntry
    │       ├── Changed entry (content_hash differs) → update DocketEntry, mark embedded=False
    │       └── Unchanged → skip
    │
    ├── 4. Reconcile documents (for all new/changed entries)
    │       ├── For each PDF URL in RSS entry not yet in DocketEntryDocument → insert row
    │       └── For each DocketEntryDocument with downloaded=False → download (downloader.py)
    │
    ├── 5. Embed all entries where embedded=False (indexer.py)
    │       └── Each DocketEntry and each DocketEntryDocument is indexed as a separate
    │           LlamaIndex document, keyed by its DB id for deduplication on upsert
    │
    ├── 6. If anything changed → update case memory (memory.py)
    │       └── existing memory_text + new entry summaries → OpenAI → updated memory_text → save to DB
    │
    └── 7. Update case.last_synced_at
```

**Idempotency:** every step checks state before acting. Re-running `sync_case()` on a fully synced case is a no-op.

**Backfill:** when a case is first added, `sync_case()` is called immediately. Since the DB is empty for that case, all entries are treated as new and the full history is ingested.

**PDF availability:** only PDFs with a direct CourtListener `.pdf` URL are downloaded. Entries where no PDF is available are stored and embedded using text content only. If a PDF URL appears in a future poll for an existing entry, it is detected during document reconciliation (step 4) and downloaded then.

---

## Scheduler: `scheduler.py`

Uses APScheduler `AsyncIOScheduler`. Exposes three operations:

```python
start()               # Start scheduler; re-register jobs for all existing cases from DB
add_case(case_id)     # Register interval job + trigger immediate sync_case() for backfill
remove_case(case_id)  # Remove the interval job when a case is deleted
```

Each case has its own named interval job keyed by `court_listener_id`, running every `settings.poll_interval_seconds`. All case pipelines run as concurrent `asyncio` tasks — one slow case never blocks another.

On bot restart, `start()` reloads all cases from the DB and re-registers their jobs automatically.

---

## Error Handling

| Failure | Behavior |
|---------|----------|
| RSS fetch fails | `stamina.retry` with exponential backoff; if exhausted, abort sync, do not update `last_synced_at`, retry on next scheduled poll |
| PDF download fails | `stamina.retry` with exponential backoff; if exhausted, leave `downloaded=False`, retried naturally on next poll via reconciliation |
| Embedding fails | Leave `embedded=False`, retried on next poll |
| Unhandled exception in a case pipeline | Caught at the task level, logged with case ID, does not affect other cases running concurrently |

All errors are logged with Loguru including `case_id` and `court_listener_id` for traceability.

---

## Internal Pydantic Models

`RawEntry` — parsed from feedparser, represents one item from the RSS feed before it is reconciled against the DB:

```python
class RawEntry(BaseModel):
    court_listener_id: str
    title: str
    content: str
    content_hash: str
    date_filed: datetime
    pdf_urls: list[str]   # all .pdf links found in the entry
```

`SyncResult` — returned by `sync_case()`, used for logging and Discord status responses:

```python
class SyncResult(BaseModel):
    case_id: str
    new_entries: int
    updated_entries: int
    new_documents: int
    downloaded_documents: int
    memory_updated: bool
    errors: list[str]
```
