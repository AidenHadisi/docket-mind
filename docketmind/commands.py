"""Bot commands: types, handlers, and the COMMANDS registry."""

import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import NamedTuple

from loguru import logger
from sqlalchemy.exc import IntegrityError

from docketmind import schedule, store
from docketmind.chat import query
from docketmind.configure import settings
from docketmind.index import delete_case_vectors
from docketmind.ingest import fetch_case_metadata
from docketmind.platforms import BotResponse, PermissionLevel, PlatformEvent

type CommandHandler = Callable[[PlatformEvent], Awaitable[BotResponse]]


class CommandParam(NamedTuple):
    """One parameter accepted by a bot command."""

    name: str
    type: type
    description: str
    required: bool = True


@dataclass(frozen=True, slots=True)
class CommandSpec:
    """Declarative definition of a bot command.

    Platform adapters consume these to build native UI elements (e.g. Discord
    slash commands). The dispatch loop uses them to enforce permissions and
    cooldowns before invoking the handler.
    """

    name: str
    description: str
    handler: CommandHandler
    params: list[CommandParam] = field(default_factory=list)
    cooldown: float = 0.0
    permission: PermissionLevel = PermissionLevel.USER
    ephemeral_defer: bool = False


class PermissionDeniedError(Exception):
    """Raised when a command requires a higher permission level than the caller has."""


class CooldownError(Exception):
    """Raised when a command is invoked before its cooldown expires."""

    def __init__(self, retry_after: float) -> None:
        """Initialise with the number of seconds remaining on the cooldown."""
        self.retry_after = retry_after
        super().__init__(f"Command on cooldown. Retry after {retry_after:.1f}s.")


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


async def ask(event: PlatformEvent) -> BotResponse:
    """Answer a question using RAG, optionally scoped to a case."""
    question: str = event.args["question"]
    case_id: str | None = event.args.get("case_id")
    result = await query(question, case_id=case_id)
    return BotResponse(text=result.answer, citations=result.sources, question=question)


async def add_case(event: PlatformEvent) -> BotResponse:
    """Register a new case and trigger an immediate backfill sync.

    Fetches case metadata from the CourtListener RSS feed, inserts a Case row,
    then hands off to the ingest scheduler for backfill and polling.
    """
    court_listener_id: str = event.args["court_listener_id"]

    async with store.async_session() as session:
        if await store.get_case_by_court_listener_id(session, court_listener_id):
            return BotResponse(
                text=f"Case `{court_listener_id}` is already being tracked.",
                ephemeral=True,
            )

        rss_url = f"https://www.courtlistener.com/docket/{court_listener_id}/feed/"
        try:
            name = await fetch_case_metadata(rss_url)
        except Exception as exc:
            logger.error("Failed to fetch metadata for case {}: {}", court_listener_id, exc)
            return BotResponse(
                text=f"Could not fetch feed for `{court_listener_id}`."
                " Check that the ID is correct.",
                ephemeral=True,
            )

        case = store.Case(court_listener_id=court_listener_id, name=name)
        session.add(case)
        try:
            await session.commit()
        except IntegrityError:
            return BotResponse(
                text=f"Case `{court_listener_id}` is already being tracked.",
                ephemeral=True,
            )
        await session.refresh(case)

    await schedule.add_case(case.id)
    return BotResponse(text=f"Now tracking **{name}** (`{court_listener_id}`).")


async def remove_case(event: PlatformEvent) -> BotResponse:
    """Remove a tracked case and its scheduled sync job.

    Deletes the Case row (cascade-deletes entries and documents) and removes
    the polling job from the scheduler.
    """
    court_listener_id: str = event.args["court_listener_id"]

    async with store.async_session() as session:
        case = await store.get_case_by_court_listener_id(session, court_listener_id)
        if case is None:
            return BotResponse(
                text=f"Case `{court_listener_id}` is not being tracked.",
                ephemeral=True,
            )
        case_id = case.id
        name = case.name
        await session.delete(case)
        await session.commit()

    schedule.remove_case(case_id)
    delete_case_vectors(case_id)

    pdf_dir = settings.pdfs_path / court_listener_id
    if pdf_dir.is_dir():
        shutil.rmtree(pdf_dir)
        logger.info("Removed cached PDFs at {}", pdf_dir)

    return BotResponse(
        text=f"Stopped tracking **{name}** (`{court_listener_id}`).",
        ephemeral=True,
    )


def _fmt_time(dt: datetime | None) -> str:
    """Format a datetime as a human-readable string, or 'never' if None."""
    return dt.strftime("%Y-%m-%d %H:%M UTC") if dt else "never"


async def list_cases(event: PlatformEvent) -> BotResponse:
    """List all currently tracked cases with their last-synced time."""
    async with store.async_session() as session:
        cases = await store.list_cases(session)

    if not cases:
        return BotResponse(text="No cases are currently being tracked.")

    lines = [
        f"**{c.name}** (`{c.court_listener_id}`) — last synced: {_fmt_time(c.last_synced_at)}"
        for c in cases
    ]
    return BotResponse(text="**Tracked Cases:**\n" + "\n".join(lines))


# ---------------------------------------------------------------------------
# Command registry
# ---------------------------------------------------------------------------

COMMANDS: list[CommandSpec] = [
    CommandSpec(
        name="ask",
        description="Ask a question about a tracked case",
        handler=ask,
        params=[
            CommandParam("question", str, "The question to ask"),
            CommandParam("case_id", str, "Scope to a specific case ID", required=False),
        ],
        cooldown=30.0,
    ),
    CommandSpec(
        name="add_case",
        description="Start tracking a CourtListener case",
        handler=add_case,
        params=[
            CommandParam("court_listener_id", str, "CourtListener docket ID (numeric)"),
        ],
        permission=PermissionLevel.ADMIN,
        ephemeral_defer=True,
    ),
    CommandSpec(
        name="remove_case",
        description="Stop tracking a CourtListener case",
        handler=remove_case,
        params=[
            CommandParam("court_listener_id", str, "CourtListener docket ID to remove"),
        ],
        permission=PermissionLevel.ADMIN,
        ephemeral_defer=True,
    ),
    CommandSpec(
        name="list_cases",
        description="List all currently tracked cases",
        handler=list_cases,
    ),
]
