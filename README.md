# DocketMind

DocketMind is an AI-powered legal docket assistant that tracks lawsuits, indexes court filings, and answers questions about active cases in real time. It runs as a Discord bot, giving teams instant access to up-to-date case intelligence through natural language queries.

---

## Overview

Legal teams, journalists, researchers, and advocates often need to track dozens of active lawsuits simultaneously. CourtListener publishes docket entries for federal cases via RSS, but reading raw docket text is time-consuming and requires legal context to interpret.

DocketMind bridges that gap. Administrators subscribe the bot to cases. The bot continuously monitors CourtListener RSS feeds, pulls every new docket entry, and indexes both the text and any attached PDF documents into a vector store. A per-case memory keeps a running summary of each lawsuit's status. When users ask questions, the bot retrieves relevant documents using RAG and answers with an LLM — with citations back to the original filings.

---

## Features

### Case Management
- Admins add or remove cases via bot commands on any supported platform
- Each case is tracked by its CourtListener case identifier
- Cases include metadata: case name, court, docket number, parties, date added

### Automated Docket Monitoring
- Polls CourtListener RSS feeds for new docket entries on a configurable schedule
- Each new entry is saved locally with full metadata (date filed, document type, filing party, description)
- PDF attachments linked in the RSS entry are downloaded and stored alongside the entry

### Document Indexing & Embeddings
- All docket entry text and PDF content is chunked and embedded into a vector store
- Embeddings are updated incrementally — only new or changed content is re-indexed
- Supports multi-page PDFs with page-level chunking for precise retrieval

### Per-Case Memory
- Each lawsuit has a persistent memory document that is updated whenever new entries arrive
- The memory summarizes: current posture, recent filings, key rulings, upcoming deadlines, and major parties/arguments
- Memory is used alongside RAG results to give the LLM rich context without overloading the prompt

### Natural Language Q&A
- Users ask questions in plain English directly in Discord
- The bot retrieves relevant chunks via vector similarity search and passes them to the LLM with the case memory
- Responses include source citations (docket entry number, document name, filing date)
- Supports multi-turn conversation within a thread or session

### Manual Document Ingestion
- Admins can upload PDF files directly to a case via bot commands
- Manually added documents follow the same ingestion pipeline: stored, chunked, embedded, and reflected in case memory
- Useful for adding documents not on CourtListener (exhibits, state filings, private correspondence)

### Platform Support
- Runs on Discord
- Slash commands for admin operations
- Designed to support additional platforms (Slack, Telegram) in the future

---

## Supported Platforms

| Platform | User Q&A | Admin Commands | File Upload |
|----------|----------|----------------|-------------|
| Discord  | Yes      | Yes            | Yes         |
| Slack    | Planned  | Planned        | Planned     |
| Telegram | Planned  | Planned        | Planned     |

---

## Admin Commands

All commands are Discord slash commands.

| Command | Description |
|---------|-------------|
| `/docket add <case-id>` | Subscribe to a CourtListener case by ID or URL |
| `/docket remove <case-id>` | Unsubscribe and stop monitoring a case |
| `/docket list` | List all currently tracked cases |
| `/docket status <case-id>` | Show latest sync status and entry count for a case |
| `/docket upload <case-id>` | Attach a PDF to a specific case (via file upload) |
| `/docket sync <case-id>` | Manually trigger an RSS sync for a case |

---

## Architecture

```
Discord Bot
        │
        ▼
   Command Router
        │
   ┌────┴──────────────────────┐
   │                           │
Admin Commands           User Q&A Handler
   │                           │
   ▼                           ▼
Case Registry           Retrieval Pipeline
   │                      │           │
   ▼                  Vector       Case Memory
RSS Poller            Search           │
   │                      └─────┬──────┘
   ▼                            ▼
Ingestion Pipeline           LLM (with citations)
  ├── Docket Entry Parser          │
  ├── PDF Downloader               ▼
  ├── Text Chunker           Response → Discord
  ├── Embedder
  └── Memory Updater
```

---

## Data Sources

- **CourtListener RSS Feeds** — docket entries for federal cases, including PACER document links
- **CourtListener PACER API** — optionally used to fetch full document metadata
- **Manually Uploaded PDFs** — documents provided directly by admins via bot commands

---

## Technology Stack (Planned)

| Layer | Technology |
|-------|------------|
| Runtime | Python 3.13+ |
| Package Management | uv |
| Linting & Formatting | Ruff |
| Type Checking | Pyright |
| Data Validation | Pydantic v2 |
| Configuration | pydantic-settings (`.env` + environment variables) |
| Git Hooks | pre-commit |
| Bot Framework | discord.py |
| LLM | OpenAI (via LlamaIndex) |
| Embeddings | OpenAI `text-embedding-3-small` (via LlamaIndex) |
| Vector Store | LlamaIndex SimpleVectorStore (disk-persisted) |
| Database | SQLite |
| ORM | SQLAlchemy 2.0 |
| DB Migrations | Alembic |
| Logging | Loguru |
| Testing | pytest, pytest-asyncio, pytest-cov, respx, pytest-mock, factory-boy |
| PDF Parsing | LlamaIndex built-in document loaders |
| Chunking | LlamaIndex text splitters |
| RAG Pipeline | LlamaIndex query engine |
| HTTP Client | httpx (async) |
| Retries | stamina |
| Async File I/O | aiofiles |
| RSS Parsing | feedparser |
| RSS Polling | APScheduler (in-process interval scheduler) |
| Storage | Local filesystem (PDFs and raw docket files) |

---

## Requirements

### Functional Requirements

1. The system must monitor CourtListener RSS feeds for new docket entries on each tracked case
2. New docket entries must be stored with full metadata within 15 minutes of publication
3. PDF attachments in docket entries must be automatically downloaded and indexed
4. Each case must maintain an up-to-date summary memory refreshed on every new entry
5. Users must be able to ask free-form questions about any tracked case
6. Answers must cite the source docket entry or document
7. Admins must be able to add and remove cases via bot commands
8. Admins must be able to manually upload PDFs to a specific case
9. The bot must operate on Discord
10. Admin commands must be restricted to users with an admin role on the platform

### Non-Functional Requirements

1. **Latency** — Q&A responses must complete within 10 seconds under normal load
2. **Reliability** — RSS polling must recover from transient failures without losing entries
3. **Idempotency** — Re-syncing a case must not create duplicate entries or embeddings
4. **Scalability** — The system must handle at least 100 tracked cases concurrently
5. **Security** — Admin commands must be gated behind platform-level role checks
6. **Observability** — All ingestion events and query traces must be logged for debugging

---

## Project Structure (Planned)

```
docket-mind/
├── docketmind/
│   ├── ingestion/     # RSS polling, PDF download, chunking, embedding, memory updates
│   ├── intelligence/  # RAG pipeline, LLM, citations, Q&A
│   ├── bot/
│   │   ├── base.py        # Abstract platform interface
│   │   ├── discord/       # Discord adapter (slash commands, event handlers)
│   │   ├── slack/         # (future)
│   │   └── telegram/      # (future)
│   └── config.py          # pydantic-settings configuration
├── data/              # Local storage (SQLite DB, ChromaDB index, PDFs)
├── tests/
├── alembic/
├── docs/
├── pyproject.toml
└── README.md
```

---

## Getting Started

> Setup instructions will be added once the initial implementation is complete.

Prerequisites:
- Python 3.13+
- uv
- Anthropic API key
- OpenAI API key (for embeddings)
- Discord bot token

---

## License

MIT
