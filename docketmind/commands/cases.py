"""Case management commands: add, remove, and list tracked cases."""

from datetime import datetime

from loguru import logger
from sqlalchemy.exc import IntegrityError

from docketmind import schedule, store
from docketmind.commands import CommandParam, command
from docketmind.ingest import fetch_case_metadata
from docketmind.platforms import BotResponse, PermissionLevel, PlatformEvent


def _fmt_time(dt: datetime | None) -> str:
    """Format a datetime as a human-readable string, or 'never' if None."""
    return dt.strftime("%Y-%m-%d %H:%M UTC") if dt else "never"


@command(
    name="add_case",
    description="Start tracking a CourtListener case",
    params=[
        CommandParam("court_listener_id", str, "CourtListener docket ID (numeric)"),
    ],
    permission=PermissionLevel.ADMIN,
    ephemeral_defer=True,
)
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
            name, court = await fetch_case_metadata(rss_url)
        except Exception as exc:
            logger.error("Failed to fetch metadata for case {}: {}", court_listener_id, exc)
            return BotResponse(
                text=f"Could not fetch feed for `{court_listener_id}`."
                " Check that the ID is correct.",
                ephemeral=True,
            )

        case = store.Case(court_listener_id=court_listener_id, name=name, court=court)
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
    return BotResponse(text=f"Now tracking **{name}** (`{court_listener_id}`) — {court}.")


@command(
    name="remove_case",
    description="Stop tracking a CourtListener case",
    params=[
        CommandParam("court_listener_id", str, "CourtListener docket ID to remove"),
    ],
    permission=PermissionLevel.ADMIN,
    ephemeral_defer=True,
)
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
    return BotResponse(
        text=f"Stopped tracking **{name}** (`{court_listener_id}`).",
        ephemeral=True,
    )


@command(
    name="list_cases",
    description="List all currently tracked cases",
)
async def list_cases(event: PlatformEvent) -> BotResponse:
    """List all currently tracked cases with their last-synced time."""
    async with store.async_session() as session:
        cases = await store.list_cases(session)

    if not cases:
        return BotResponse(text="No cases are currently being tracked.")

    lines = [
        f"**{c.name}** (`{c.court_listener_id}`) — {c.court} — last synced: {_fmt_time(c.last_synced_at)}"  # noqa: E501
        for c in cases
    ]
    return BotResponse(text="**Tracked Cases:**\n" + "\n".join(lines))
