"""Per-case memory updater: summarizes new docket entries via OpenAI."""

from openai import AsyncOpenAI

from docketmind.config import settings
from docketmind.models import Case, DocketEntry

_client = AsyncOpenAI(api_key=settings.openai_api_key)


async def update_case_memory(case: Case, new_entries: list[DocketEntry]) -> str:
    """Generate an updated memory summary for a case given new docket entries.

    Passes the existing memory (if any) and the new entries to the LLM
    and returns the updated summary text to be stored in Case.memory_text.
    """
    entries_text = "\n\n".join(
        f"[{e.date_filed.strftime('%Y-%m-%d')}] {e.title}\n{e.content}" for e in new_entries
    )

    current_summary = case.memory_text or "No summary yet — this is the first batch of entries."

    prompt = (
        "You are a legal analyst tracking federal lawsuits. "
        "Update the case summary below with the new docket entries provided. "
        "Cover: current posture, recent key filings, notable rulings, "
        "upcoming deadlines, and major parties/arguments.\n\n"
        f"Current summary:\n{current_summary}\n\n"
        f"New docket entries:\n{entries_text}\n\n"
        "Write a concise updated summary (2-4 paragraphs)."
    )

    response = await _client.chat.completions.create(
        model=settings.openai_llm_model,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content or ""
