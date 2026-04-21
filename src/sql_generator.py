"""
src/sql_generator.py
--------------------
Takes a natural language IPL question and returns an Athena SQL query.
Uses Claude 3.5 Haiku via Bedrock with the full schema context injected
as the system prompt.

Temperature is set to 0 (deterministic) — critical for SQL generation.
"""

from __future__ import annotations
import os
import re
from src.bedrock_client import invoke, MAX_TOKENS_SQL

# Path to schema context file (relative to project root)
SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "..", "prompts", "schema_context.txt")

_schema_context: str | None = None        # Cached on first load
_schema_mtime: float = 0.0                # Last-modified time of schema file

# ── Player name resolution table ───────────────────────────────────────────
# Maps common player name variants → (surname_for_like, column, team_hint)
# team_hint is injected when the surname is ambiguous (e.g., multiple Sharmas)
# column: "striker" for batters, "bowler" for bowlers, "both" for allrounders
_PLAYER_HINTS: list[tuple[list[str], str, str, str | None]] = [
    # (name_variants,                   surname_like,  column,   team_hint)
    (["rohit sharma", "rohit"],          "sharma",     "striker", "Mumbai Indians"),
    (["virat kohli", "kohli"],           "kohli",      "striker", None),
    (["jasprit bumrah", "bumrah"],       "bumrah",     "bowler",  None),
    (["ms dhoni", "dhoni", "msd"],       "dhoni",      "both",    None),
    (["hardik pandya"],                  "pandya",     "both",    "Mumbai Indians"),
    (["krunal pandya"],                  "pandya",     "both",    "Lucknow Super Giants"),
    (["ravindra jadeja", "jadeja"],      "jadeja",     "both",    None),
    (["kl rahul", "k l rahul"],          "rahul",      "striker", "Lucknow Super Giants"),
    (["suresh raina", "raina"],          "raina",      "striker", "Chennai Super Kings"),
    (["shikhar dhawan", "dhawan"],       "dhawan",     "striker", None),
    (["gautam gambhir", "gambhir"],      "gambhir",    "striker", None),
    (["sachin tendulkar", "tendulkar"],  "tendulkar",  "striker", None),
    (["david warner", "warner"],         "warner",     "striker", None),
    (["ab de villiers", "de villiers"],  "villiers",   "striker", None),
    (["chris gayle", "gayle"],           "gayle",      "striker", None),
    (["yuzvendra chahal", "chahal"],     "chahal",     "bowler",  None),
    (["ravichandran ashwin", "ashwin"],  "ashwin",     "bowler",  None),
    (["bhuvneshwar kumar", "bhuvi"],     "kumar",      "bowler",  "Sunrisers Hyderabad"),
    (["lasith malinga", "malinga"],      "malinga",    "bowler",  None),
    (["pat cummins", "cummins"],         "cummins",    "bowler",  None),
    (["trent boult", "boult"],           "boult",      "bowler",  None),
    (["mohammed shami", "shami"],        "shami",      "bowler",  None),
    (["mohammed siraj", "siraj"],        "siraj",      "bowler",  None),
    (["rishabh pant", "pant"],           "pant",       "striker", "Delhi Capitals"),
    (["sanju samson", "samson"],         "samson",     "striker", "Rajasthan Royals"),
    (["faf du plessis", "du plessis"],   "plessis",    "striker", None),
    (["kane williamson", "williamson"],  "williamson", "striker", None),
    (["andre russell", "russell"],       "russell",    "both",    "Kolkata Knight Riders"),
    (["suryakumar yadav", "suryakumar"], "yadav",      "striker", "Mumbai Indians"),
    (["ishan kishan", "ishan"],          "kishan",     "striker", None),
]


def _load_schema() -> str:
    """Load schema context file, reloading if the file has changed on disk.
    This prevents stale prompts after schema edits without a full app restart.
    """
    global _schema_context, _schema_mtime
    try:
        current_mtime = os.path.getmtime(SCHEMA_PATH)
    except OSError:
        current_mtime = 0.0

    if _schema_context is None or current_mtime != _schema_mtime:
        with open(SCHEMA_PATH, "r") as f:
            _schema_context = f.read()
        _schema_mtime = current_mtime

    return _schema_context


def _build_player_hint(question: str) -> str:
    """
    Detect player names in the question and return an explicit CTE hint string
    injected into the user message before SQL generation.

    This tells Claude exactly which LIKE pattern and team filter to use in the
    player_names CTE, eliminating guesswork about name formats.

    Returns empty string if no known player is detected.
    """
    q_lower = question.lower()
    hints = []

    for name_variants, surname, column, team_hint in _PLAYER_HINTS:
        if any(variant in q_lower for variant in name_variants):
            col_label = {
                "striker": "striker (batting)",
                "bowler":  "bowler (bowling)",
                "both":    "striker or bowler (allrounder)"
            }.get(column, column)

            hint = (
                f"PLAYER DETECTED: Use self-resolving CTE → "
                f"player_names AS (SELECT DISTINCT {column.replace('both','striker')} AS player_name "
                f"FROM ipl_analytics_db.deliveries_pq "
                f"WHERE LOWER({column.replace('both','striker')}) LIKE '%{surname}%'"
            )
            if team_hint:
                hint += f" AND {'batting_team' if column != 'bowler' else 'bowling_team'} = '{team_hint}'"
            hint += f") — column to use: {col_label}"
            hints.append(hint)
            break   # Only inject hint for the first matched player

    return "\n".join(hints)


def _detect_venue_season_combo(question: str) -> bool:
    """Returns True if question contains both a venue/city AND a season reference.
    Used to add a caution note about venue availability gaps (e.g., 2020 UAE, 2022 DY Patil).
    """
    q_lower = question.lower()
    has_venue = any(w in q_lower for w in [
        "stadium", "venue", "ground", "wankhede", "eden", "chinnaswamy",
        "kotla", "chepauk", "mumbai", "delhi", "kolkata", "chennai",
        "bangalore", "bengaluru", "hyderabad", "jaipur", "mohali", "ahmedabad"
    ])
    has_season = any(w in q_lower for w in [
        "season", "year", "last", "recent", "2020", "2021", "2022",
        "2023", "2024", "2025", "ipl 20"
    ])
    return has_venue and has_season


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
        ValueError  if Claude returns unexpected output
        RuntimeError if Bedrock call fails
    """
    schema = _load_schema()

    # ── Build conversation history context ───────────────────────────────
    history_context = ""
    if conversation_history:
        recent = conversation_history[-6:]   # last 3 turns = 6 messages
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

    # ── Inject player name resolution hint ───────────────────────────────
    player_hint = _build_player_hint(question)

    # ── Inject venue/season gap warning ──────────────────────────────────
    venue_caution = ""
    if _detect_venue_season_combo(question):
        venue_caution = (
            "\nVENUE CAUTION: If the venue+season combination returns no data, "
            "remove the venue filter and search across all venues. "
            "IPL 2020 was in UAE (no Indian venues). "
            "Mumbai Indians played at DY Patil/Brabourne in 2022 (not Wankhede)."
        )

    # ── Assemble final user message ───────────────────────────────────────
    parts = []
    if history_context:
        parts.append(history_context)
    if player_hint:
        parts.append(player_hint)
    if venue_caution:
        parts.append(venue_caution)
    parts.append(f"Question: {question}")

    user_message = "\n\n".join(parts).strip()

    # ── Call Bedrock ──────────────────────────────────────────────────────
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
    """Remove ```sql ... ``` or ``` ... ``` fences if present.
    Also strips trailing semicolons and truncates at first semicolon —
    Athena rejects multi-statement queries and trailing semicolons.
    """
    # Remove opening fence
    text = re.sub(r"^```(?:sql)?\s*\n?", "", text.strip(), flags=re.IGNORECASE)
    # Remove closing fence
    text = re.sub(r"\n?```\s*$", "", text.strip())
    text = text.strip()

    # Truncate at first semicolon — Athena only allows one SQL statement.
    if ";" in text:
        text = text.split(";")[0].strip()

    return text


def is_out_of_scope(question: str) -> bool:
    """
    Lightweight check: is this question clearly not answerable from IPL data?
    Returns True if the question is about a completely unrelated topic.
    """
    question_lower = question.lower()

    out_of_scope_keywords = [
        "football", "soccer", "nba", "nfl", "tennis", "golf",
        "recipe", "weather", "stock", "crypto", "movie", "song",
        "politics", "covid", "war", "news"
    ]

    for kw in out_of_scope_keywords:
        if kw in question_lower:
            return True

    return False
