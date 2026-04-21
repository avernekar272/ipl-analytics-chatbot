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
10. Do NOT include markdown formatting like **bold** or bullet points."""


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
    if not query_results:
        return (
            "No data found for this query. This could be because the player name "
            "spelling doesn't match exactly, the season doesn't exist in the dataset, "
            "or the filter combination returns no results.\n\n"
            "Try rephrasing with the full player name or a different season."
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
