"""
src/sql_generator.py
--------------------
Takes a natural language IPL question and returns an Athena SQL query.
Uses Claude 3 Haiku via Bedrock with the full schema context injected
as the system prompt.

Temperature is set to 0 (deterministic) — critical for SQL generation.
"""

import os
import re
from src.bedrock_client import invoke, MAX_TOKENS_SQL

# Path to schema context file (relative to project root)
SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "..", "prompts", "schema_context.txt")

_schema_context: str | None = None   # Cached on first load


def _load_schema() -> str:
    """Load and cache the schema context file."""
    global _schema_context
    if _schema_context is None:
        with open(SCHEMA_PATH, "r") as f:
            _schema_context = f.read()
    return _schema_context


def generate_sql(question: str, conversation_history: list[dict] | None = None) -> str:
    """
    Generate Athena SQL from a natural language IPL question.

    Args:
        question             : str  — User's question in plain English
        conversation_history : list — Prior turns for follow-up resolution.
                               Each entry: {"role": "user"|"assistant", "content": str}

    Returns:
        str — A valid Athena SQL query (no markdown, no explanation)

    Raises:
        ValueError  if Claude returns something that doesn't look like SQL
        RuntimeError if Bedrock call fails
    """
    schema = _load_schema()

    # Build context string from conversation history (last 3 turns max)
    history_context = ""
    if conversation_history:
        recent = conversation_history[-6:]   # 3 turns = 6 messages
        history_lines = []
        for msg in recent:
            prefix = "User" if msg["role"] == "user" else "Assistant"
            history_lines.append(f"{prefix}: {msg['content'][:300]}")
        if history_lines:
            history_context = (
                "\n\nCONVERSATION HISTORY (for follow-up context):\n"
                + "\n".join(history_lines)
                + "\n\nNow answer the new question using context above if relevant."
            )

    user_message = f"{history_context}\n\nQuestion: {question}".strip()

    raw_sql = invoke(
        system_prompt=schema,
        user_message=user_message,
        max_tokens=MAX_TOKENS_SQL,
    )

    # Strip markdown code fences if Claude wraps in ```sql ... ```
    sql = _strip_markdown(raw_sql)

    # Basic sanity check — must start with SELECT or WITH
    if not re.match(r"^\s*(SELECT|WITH|--)", sql, re.IGNORECASE):
        raise ValueError(
            f"Claude returned unexpected output (not SQL):\n{sql[:300]}"
        )

    return sql


def _strip_markdown(text: str) -> str:
    """Remove ```sql ... ``` or ``` ... ``` fences if present."""
    # Remove opening fence
    text = re.sub(r"^```(?:sql)?\s*\n?", "", text.strip(), flags=re.IGNORECASE)
    # Remove closing fence
    text = re.sub(r"\n?```\s*$", "", text.strip())
    return text.strip()


def is_out_of_scope(question: str) -> bool:
    """
    Lightweight check: is this question clearly not answerable from IPL data?
    Returns True if the question is about a completely unrelated topic.
    We do a simple keyword check to avoid wasting a Bedrock call.
    """
    question_lower = question.lower()

    # Topics clearly outside IPL cricket data scope
    out_of_scope_keywords = [
        "football", "soccer", "nba", "nfl", "tennis", "golf",
        "recipe", "weather", "stock", "crypto", "movie", "song",
        "politics", "covid", "war", "news"
    ]

    for kw in out_of_scope_keywords:
        if kw in question_lower:
            return True

    return False
