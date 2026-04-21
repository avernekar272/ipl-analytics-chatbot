"""
src/answer_formatter.py
-----------------------
Takes Athena query results + original question and formats a clean
natural language answer using Claude via Bedrock.

Rules enforced:
  - Answer under 150 words
  - Starts with the headline stat (the direct answer)
  - Adds 1-2 sentences of context
  - Notes the SQL and seasons referenced (transparency)
  - Suggests a follow-up question when relevant
"""

from __future__ import annotations
from src.bedrock_client import invoke, MAX_TOKENS_ANSWER
from src.athena_client import format_results_as_table

# System prompt for the answer formatter (separate from SQL generation)
ANSWER_SYSTEM_PROMPT = """You are a cricket analytics assistant answering questions about IPL data.

RULES:
1. Start with the direct answer as the first sentence (the headline stat).
2. Add 1-2 sentences of useful context (comparison, trend, or significance).
3. Keep the total answer under 150 words.
4. Be specific — use the exact numbers from the data provided.
5. Do NOT make up stats. Only use data from the results table below.
6. If the result table is empty, say "No data found for this query" and suggest why.
7. End with one suggested follow-up question the user might find interesting.
8. Write in a conversational but precise tone — like a knowledgeable cricket analyst.
9. Do NOT repeat the question back. Start directly with the answer.
10. Do NOT include markdown formatting like **bold** or bullet points.

ANTI-HALLUCINATION RULES — HIGHEST PRIORITY:
11. The DATA RESULTS TABLE is the ONLY source of truth. Every number in your answer MUST
    come directly from that table. Never invent, estimate, or recall numbers from training data.
12. If the user's question contains a specific number (e.g. "you said CSK won 18 times"),
    IGNORE that number entirely. Report only what the data results table shows.
13. Never say "you are correct", "that's right", or "indeed" to confirm a number the user
    stated — only confirm numbers that appear in the data results table below.
14. If the data result contradicts what the user claims, report the data result and note
    the discrepancy: "According to the data, the actual figure is X."
15. If you are uncertain whether a number in your answer comes from the data or your
    training memory, do NOT include it."""


def _is_empty_aggregation(query_results: list[dict]) -> bool:
    """
    Detect 'ghost rows' — a single row returned by SUM/COUNT on zero matches.
    SUM() on empty set returns NULL (empty string in Athena results).
    COUNT() on empty set returns 0.
    If the only row has all empty-string or zero numeric values, treat as no data.
    """
    if not query_results:
        return True
    if len(query_results) == 1:
        row = query_results[0]
        non_empty_values = [v for v in row.values() if str(v).strip() not in ("", "0", "0.0", "0.00")]
        if not non_empty_values:
            return True
    return False


def format_answer(
    question: str,
    sql: str,
    query_results: list[dict],
    season_hint: str = ""
) -> str:
    """
    Format Athena query results into a clean natural language answer.

    Args:
        question       : str       — Original user question
        sql            : str       — SQL that was executed (for transparency)
        query_results  : list[dict]— Rows returned by Athena
        season_hint    : str       — Season(s) referenced e.g. "2024" (optional)

    Returns:
        str — Clean answer under 150 words with a follow-up suggestion
    """
    if _is_empty_aggregation(query_results):
        # Build a specific, actionable no-data message
        q_lower = question.lower()
        has_venue   = any(w in q_lower for w in ["stadium", "venue", "ground", "wankhede",
                                                   "eden", "chinnaswamy", "kotla", "chepauk",
                                                   "mumbai", "delhi", "kolkata", "chennai",
                                                   "bangalore", "hyderabad", "jaipur"])
        has_season  = any(w in q_lower for w in ["season", "year", "last", "recent",
                                                   "2021", "2022", "2023", "2024", "2025"])

        suggestions = []
        if has_venue and has_season:
            suggestions.append(
                "Try: Remove the venue — ask for stats across all stadiums in those seasons."
            )
            suggestions.append(
                "Try: Remove the season filter — ask for career stats at that specific venue."
            )
            suggestions.append(
                "Note: Some venues are not used every season (e.g., MI played at DY Patil "
                "in 2022, not Wankhede; IPL 2020 was entirely in UAE)."
            )
        elif has_venue:
            suggestions.append("Try: Ask for career stats across all venues instead.")
        elif has_season:
            suggestions.append("Try: Ask for career stats without a season restriction.")

        suggestions.append(
            "Try: Rephrase as a career-wide question e.g. 'What is Rohit Sharma's overall "
            "batting average in the IPL?'"
        )

        suggestion_text = "\n".join(f"{i+1}. {s}" for i, s in enumerate(suggestions))
        return (
            f"No data found for this specific combination of filters.\n\n"
            f"{suggestion_text}"
        )

    results_table = format_results_as_table(query_results, max_rows=15)
    row_count = len(query_results)

    user_message = (
        f"Question: {question}\n\n"
        f"Data results ({row_count} row{'s' if row_count != 1 else ''}):\n"
        f"{results_table}\n\n"
        f"SQL used: {sql[:400]}{'...' if len(sql) > 400 else ''}"
    )

    answer = invoke(
        system_prompt=ANSWER_SYSTEM_PROMPT,
        user_message=user_message,
        max_tokens=MAX_TOKENS_ANSWER,
    )

    return answer


def results_to_chart_data(query_results: list[dict]) -> dict | None:
    """
    Inspect query results and determine if a chart makes sense.
    Returns chart config dict or None if chart is not appropriate.

    Returns dict with keys:
      chart_type : "bar" | "line" | "scatter" | None
      x          : column name for x-axis
      y          : column name for y-axis
      title      : suggested chart title
    """
    if not query_results or len(query_results) < 2:
        return None   # Single row → no chart needed

    cols = list(query_results[0].keys())
    if len(cols) < 2:
        return None

    # Detect numeric columns
    numeric_cols = []
    string_cols  = []
    for col in cols:
        sample_val = query_results[0].get(col, "")
        try:
            float(str(sample_val).replace(",", ""))
            numeric_cols.append(col)
        except (ValueError, TypeError):
            string_cols.append(col)

    if not numeric_cols:
        return None

    x_col = string_cols[0] if string_cols else cols[0]
    y_col = numeric_cols[0]

    # Decide chart type based on x-axis content
    x_sample = str(query_results[0].get(x_col, "")).lower()

    # Season/year data → line chart
    if any(c in x_col.lower() for c in ["season", "year", "date"]):
        chart_type = "line"
    # Many rows (rankings) → horizontal bar
    elif len(query_results) >= 5:
        chart_type = "bar"
    else:
        chart_type = "bar"

    return {
        "chart_type": chart_type,
        "x": x_col,
        "y": y_col,
        "title": f"{y_col.replace('_', ' ').title()} by {x_col.replace('_', ' ').title()}",
        "data": query_results,
    }
