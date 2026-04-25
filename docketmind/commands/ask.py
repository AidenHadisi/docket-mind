"""Ask command: answer questions about tracked cases using RAG."""

from docketmind.chat import query
from docketmind.commands import CommandParam, command
from docketmind.platforms import BotResponse, PlatformEvent


@command(
    name="ask",
    description="Ask a question about a tracked case",
    params=[
        CommandParam("question", str, "The question to ask"),
        CommandParam("case_id", str, "Scope to a specific case ID", required=False),
    ],
    cooldown=5.0,
)
async def ask(event: PlatformEvent) -> BotResponse:
    """Answer a question using RAG, optionally scoped to a case."""
    question: str = event.args["question"]
    case_id: str | None = event.args.get("case_id")
    result = await query(question, case_id=case_id)
    text = f"**Q:** {question}\n\n{result.answer}"
    return BotResponse(text=text, citations=result.sources)
