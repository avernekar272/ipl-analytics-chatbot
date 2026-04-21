"""
src/athena_client.py
--------------------
Executes SQL against Amazon Athena and returns results as a list of dicts.
Handles async query lifecycle: Start → Poll → Fetch results.
"""

import boto3
import time
import os

AWS_REGION          = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
ATHENA_OUTPUT_BUCKET = os.environ.get(
    "ATHENA_OUTPUT_BUCKET",
    "s3://abhishek-ipl-analytics-2026/athena-results/"
)
DATABASE            = "ipl_analytics_db"

# Query timeout — Athena rarely takes > 30s on Parquet with this dataset
POLL_INTERVAL_SECS  = 1.5
MAX_WAIT_SECS       = 60


def run_query(sql: str) -> list[dict]:
    """
    Execute a SQL query on Athena and return results as a list of row dicts.

    Args:
        sql : str — Valid Athena SQL query

    Returns:
        list[dict] — Each dict is one result row, keys = column names.
                     Returns [] for empty results.

    Raises:
        RuntimeError if query fails or times out
    """
    client = boto3.client("athena", region_name=AWS_REGION)

    # ── Start execution ──────────────────────────────────────────
    try:
        execution = client.start_query_execution(
            QueryString=sql,
            QueryExecutionContext={"Database": DATABASE},
            ResultConfiguration={"OutputLocation": ATHENA_OUTPUT_BUCKET},
        )
        execution_id = execution["QueryExecutionId"]
    except Exception as e:
        raise RuntimeError(f"Failed to start Athena query: {e}")

    # ── Poll until terminal state ────────────────────────────────
    elapsed = 0
    while elapsed < MAX_WAIT_SECS:
        time.sleep(POLL_INTERVAL_SECS)
        elapsed += POLL_INTERVAL_SECS

        status_resp = client.get_query_execution(QueryExecutionId=execution_id)
        state = status_resp["QueryExecution"]["Status"]["State"]

        if state == "SUCCEEDED":
            break
        elif state in ("FAILED", "CANCELLED"):
            reason = status_resp["QueryExecution"]["Status"].get(
                "StateChangeReason", "Unknown reason"
            )
            raise RuntimeError(f"Athena query {state}: {reason}\n\nSQL:\n{sql}")
        # RUNNING or QUEUED → keep polling

    else:
        raise RuntimeError(f"Athena query timed out after {MAX_WAIT_SECS}s")

    # ── Fetch results ────────────────────────────────────────────
    try:
        results_resp = client.get_query_results(QueryExecutionId=execution_id)
    except Exception as e:
        raise RuntimeError(f"Failed to fetch Athena results: {e}")

    rows = results_resp["ResultSet"]["Rows"]
    if len(rows) <= 1:
        return []   # Header only = no data rows

    # First row is the column header
    headers = [col.get("VarCharValue", "") for col in rows[0]["Data"]]

    result = []
    for row in rows[1:]:
        values = [col.get("VarCharValue", "") for col in row["Data"]]
        result.append(dict(zip(headers, values)))

    return result


def data_scanned_kb(execution_id: str) -> float:
    """Return KB scanned for a completed query (for cost transparency)."""
    try:
        client = boto3.client("athena", region_name=AWS_REGION)
        resp = client.get_query_execution(QueryExecutionId=execution_id)
        bytes_scanned = resp["QueryExecution"]["Statistics"].get("DataScannedInBytes", 0)
        return bytes_scanned / 1024
    except Exception:
        return 0.0


def format_results_as_table(rows: list[dict], max_rows: int = 15) -> str:
    """
    Format list of dicts as a simple text table for inclusion in LLM prompts.
    Truncates to max_rows to stay within token limits.
    """
    if not rows:
        return "(no results)"

    rows = rows[:max_rows]
    headers = list(rows[0].keys())

    # Column widths
    col_widths = {h: max(len(h), max(len(str(r.get(h, ""))) for r in rows)) for h in headers}

    header_row = " | ".join(h.ljust(col_widths[h]) for h in headers)
    separator  = "-+-".join("-" * col_widths[h] for h in headers)
    data_rows  = [
        " | ".join(str(r.get(h, "")).ljust(col_widths[h]) for h in headers)
        for r in rows
    ]

    return "\n".join([header_row, separator] + data_rows)
