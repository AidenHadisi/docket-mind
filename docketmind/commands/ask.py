"""Ask command: answer questions about tracked cases using RAG."""

from llama_index.core.chat_engine.types import BaseChatEngine

from docketmind.chat import build_chat_engine, query
from docketmind.commands import Command, command
from docketmind.platforms import BotResponse, PermissionLevel, PlatformEvent

# Per-channel chat engine store: channel_id → (case_id, engine).
# Keyed by channel_id so each channel gets its own conversational context.
# The case_id is stored alongside to detect when it changes.
_chat_engines: dict[str, tuple[str, BaseChatEngine]] = {}


def evict_engines_for_case(case_id: str) -> None:
    """Remove all chat engines associated with case_id."""
    to_remove = [ch for ch, (cid, _) in _chat_engines.items() if cid == case_id]
    for ch in to_remove:
        del _chat_engines[ch]


def _get_or_create_engine(channel_id: str, case_id: str) -> BaseChatEngine:
    """Return the chat engine for channel_id, creating one if needed.

    If the case_id changes for the same channel, the stale engine is evicted
    and a fresh one is created so retrieval stays scoped to the correct case.
    """
    entry = _chat_engines.get(channel_id)
    if entry is not None and entry[0] == case_id:
        return entry[1]
    engine = build_chat_engine(case_id)
    _chat_engines[channel_id] = (case_id, engine)
    return engine


@command(
    name="ask",
    description="Ask a question about a tracked case",
    cooldown=5.0,
    permission=PermissionLevel.USER,
)
class AskCommand(Command):
    """Answer a question using RAG, optionally scoped to a case.

    Uses a per-channel chat engine for conversational context when case_id is
    provided. Falls back to a global vector search when no case_id is given.

    Expected args: {"question": str, "case_id": str | None}
    """

    async def execute(self, event: PlatformEvent) -> BotResponse:
        """Answer a question via chat engine (case-scoped) or global vector search."""
        question: str = event.args["question"]
        case_id: str | None = event.args.get("case_id")

        if case_id:
            engine = _get_or_create_engine(event.channel_id, case_id)
            llama_response = await engine.achat(question)
            return BotResponse(text=str(llama_response))

        result = await query(question)
        return BotResponse(text=result.answer, citations=result.sources)
