"""Platform-agnostic command handlers for all bot commands.

Each handler receives a PlatformEvent and returns a BotResponse.
Handlers depend on docketmind.chat, docketmind.ingest, and docketmind.store —
never on any platform-specific code.
"""

from datetime import datetime

from loguru import logger

from docketmind import store as db
from docketmind.bot import cooldown, requires_permission
from docketmind.chat import BaseChatEngine, build_chat_engine, query
from docketmind.ingest import fetch_case_metadata
from docketmind.platforms import BotResponse, PermissionLevel, PlatformEvent
from docketmind.schedule import add_case as ingest_add_case
from docketmind.schedule import remove_case as ingest_remove_case
from docketmind.store import Case
from docketmind.store import get_case_by_court_listener_id as db_get_case
from docketmind.store import list_cases as db_list_cases

# Per-channel chat engine store: channel_id → (case_id, engine).
# The case_id is stored alongside to detect when the user switches cases.
_chat_engines: dict[str, tuple[str, BaseChatEngine]] = {}


def _get_or_create_engine(channel_id: str, case_id: str) -> BaseChatEngine:
    """Return the chat engine for channel_id, creating one if needed.

    If the case_id changes for the same channel, the old engine is evicted
    and a new one is created for the new case.
    """
    entry = _chat_engines.get(channel_id)
    if entry is not None and entry[0] == case_id:
        return entry[1]
    engine = build_chat_engine(case_id)
    _chat_engines[channel_id] = (case_id, engine)
    return engine


def _evict_engines_for_case(case_id: str) -> None:
    """Remove all chat engines associated with a given case_id."""
    to_remove = [ch for ch, (cid, _) in _chat_engines.items() if cid == case_id]
    for ch in to_remove:
        del _chat_engines[ch]


def _fmt_time(dt: datetime | None) -> str:
    """Format a datetime as a human-readable string, or 'never' if None."""
    return dt.strftime("%Y-%m-%d %H:%M UTC") if dt else "never"


@cooldown(seconds=5.0, per="user")
async def ask(event: PlatformEvent) -> BotResponse:
    """Answer a question using RAG, optionally scoped to a case.

    Uses a per-channel chat engine for conversational context when case_id
    is provided. Falls back to a global vector search when no case_id is given.

    Expected args: {"question": str, "case_id": str | None}
    """
    question: str = event.args["question"]
    case_id: str | None = event.args.get("case_id")

    if case_id:
        engine = _get_or_create_engine(event.channel_id, case_id)
        llama_response = await engine.achat(question)
        answer = str(llama_response)
        # Chat engine responses don't expose source_nodes directly; return no citations
        return BotResponse(text=answer)
    else:
        result = await query(question)
        return BotResponse(text=result.answer, citations=result.sources)


@requires_permission(PermissionLevel.ADMIN)
async def add_case(event: PlatformEvent) -> BotResponse:
    """Register a new case and trigger an immediate backfill sync.

    Fetches case metadata from the CourtListener RSS feed, inserts a Case row,
    then hands off to the ingest scheduler for backfill and polling.

    Expected args: {"court_listener_id": str}
    """
    court_listener_id: str = event.args["court_listener_id"]

    async with db.async_session() as session:
        if await db_get_case(session, court_listener_id):
            return BotResponse(
                text=f"Case `{court_listener_id}` is already being tracked.",
                ephemeral=True,
            )

    rss_url = f"https://www.courtlistener.com/docket/{court_listener_id}/feed/"
    try:
        name, court = await fetch_case_metadata(rss_url)
    except Exception as exc:
        logger.error("Failed to fetch metadata for case {}: {}", court_listener_id, exc)
        return BotResponse(
            text=f"Could not fetch feed for `{court_listener_id}`. Check that the ID is correct.",
            ephemeral=True,
        )

    async with db.async_session() as session:
        case = Case(court_listener_id=court_listener_id, name=name, court=court)
        session.add(case)
        await session.commit()
        await session.refresh(case)

    await ingest_add_case(case.id)
    return BotResponse(text=f"Now tracking **{name}** (`{court_listener_id}`) — {court}.")


@requires_permission(PermissionLevel.ADMIN)
async def remove_case(event: PlatformEvent) -> BotResponse:
    """Remove a tracked case and its scheduled sync job.

    Deletes the Case row (cascade-deletes entries and documents) and removes
    the polling job from the scheduler.

    Expected args: {"court_listener_id": str}
    """
    court_listener_id: str = event.args["court_listener_id"]

    async with db.async_session() as session:
        case = await db_get_case(session, court_listener_id)
        if case is None:
            return BotResponse(
                text=f"Case `{court_listener_id}` is not being tracked.",
                ephemeral=True,
            )
        case_id = case.id
        name = case.name
        await session.delete(case)
        await session.commit()

    ingest_remove_case(case_id)
    _evict_engines_for_case(case_id)
    return BotResponse(
        text=f"Stopped tracking **{name}** (`{court_listener_id}`).",
        ephemeral=True,
    )


async def list_cases(event: PlatformEvent) -> BotResponse:
    """List all currently tracked cases with their last-synced time.

    No args required.
    """
    async with db.async_session() as session:
        cases = await db_list_cases(session)

    if not cases:
        return BotResponse(text="No cases are currently being tracked.")

    lines = [
        f"**{c.name}** (`{c.court_listener_id}`) — {c.court} — last synced: {_fmt_time(c.last_synced_at)}"  # noqa: E501
        for c in cases
    ]
    return BotResponse(text="**Tracked Cases:**\n" + "\n".join(lines))
