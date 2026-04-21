"""
IPL Analytics Project — Script: build_chain.py  (Phase 2C)
-----------------------------------------------------------
PURPOSE : End-to-end test of the full Text-to-SQL chain.
          Runs 5 curated IPL questions through:
            Question → Bedrock (SQL) → Athena → Bedrock (Answer) → Print

          If all 5 pass cleanly, Phase 2C is COMPLETE.

HOW TO RUN (from ipl_project/ root folder):
  python scripts/build_chain.py

WHAT IT TESTS:
  Q1 — Season aggregation  : Top run scorers in IPL 2024
  Q2 — Career stat         : Virat Kohli's total IPL runs
  Q3 — Bowling stat        : Jasprit Bumrah's wickets in 2024
  Q4 — Team performance    : Mumbai Indians win history by season
  Q5 — Follow-up context   : "And in 2023?" (tests conversation memory)

EXPECTED OUTCOME:
  Each question prints: SQL → Result rows → Clean answer → Optional chart flag
  0 errors = Phase 2C done. Move to Phase 3 (Semantic Layer).
"""

import sys
import os
import time

# Add project root to path so `src` imports work
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.sql_generator    import generate_sql, is_out_of_scope
from src.athena_client    import run_query, format_results_as_table
from src.answer_formatter import format_answer, results_to_chart_data

# ── Test questions ────────────────────────────────────────────────
TEST_QUESTIONS = [
    {
        "id"      : "Q1",
        "label"   : "Season aggregation — Top run scorers",
        "question": "Who were the top 5 run scorers in IPL 2024?",
    },
    {
        "id"      : "Q2",
        "label"   : "Career stat — Total runs",
        "question": "What are Virat Kohli's total IPL runs across all seasons?",
    },
    {
        "id"      : "Q3",
        "label"   : "Bowling stat — Season wickets",
        "question": "How many wickets did Jasprit Bumrah take in IPL 2024?",
    },
    {
        "id"      : "Q4",
        "label"   : "Team matches — Count by season",
        "question": "How many matches did Mumbai Indians play in each IPL season?",
    },
    {
        "id"      : "Q5",
        "label"   : "Follow-up — Conversation memory test",
        "question": "And what about in IPL 2023?",
        "prior_qa": [
            {"role": "user",      "content": "Who were the top 5 run scorers in IPL 2024?"},
            {"role": "assistant", "content": "In IPL 2024, Virat Kohli led with 741 runs..."},
        ],
    },
]


def run_test(test: dict) -> dict:
    """Run one test question through the full chain. Returns result dict."""
    question = test["question"]
    history  = test.get("prior_qa", [])
    result   = {"id": test["id"], "label": test["label"], "question": question}

    t0 = time.time()

    # ── Out-of-scope check ────────────────────────────────────────
    if is_out_of_scope(question):
        result["status"]  = "SKIP"
        result["message"] = "Question flagged as out-of-scope (non-IPL topic)"
        return result

    # ── Step 1: Generate SQL ──────────────────────────────────────
    try:
        sql = generate_sql(question, conversation_history=history)
        result["sql"] = sql
    except Exception as e:
        result["status"]  = "FAIL"
        result["error"]   = f"SQL generation failed: {e}"
        return result

    # ── Step 2: Execute SQL in Athena ─────────────────────────────
    try:
        rows = run_query(sql)
        result["row_count"] = len(rows)
        result["rows"]      = rows
    except Exception as e:
        result["status"]  = "FAIL"
        result["error"]   = f"Athena execution failed: {e}"
        result["sql"]     = sql
        return result

    # ── Step 3: Format answer ─────────────────────────────────────
    try:
        answer = format_answer(question, sql, rows)
        result["answer"] = answer
    except Exception as e:
        result["status"]  = "FAIL"
        result["error"]   = f"Answer formatting failed: {e}"
        return result

    # ── Step 4: Chart decision ────────────────────────────────────
    chart_config = results_to_chart_data(rows)
    result["chart_type"] = chart_config["chart_type"] if chart_config else "none"

    result["status"]       = "PASS"
    result["elapsed_secs"] = round(time.time() - t0, 1)
    return result


# ── Main runner ───────────────────────────────────────────────────

def main():
    print("=" * 70)
    print("  IPL ANALYTICS — PHASE 2C: FULL CHAIN TEST")
    print("  Question → Bedrock SQL → Athena → Bedrock Answer")
    print("=" * 70)

    results = []
    passed  = 0
    failed  = 0

    for i, test in enumerate(TEST_QUESTIONS, 1):
        print(f"\n{'─' * 70}")
        print(f"  [{test['id']}] {test['label']}")
        print(f"  Q: {test['question']}")
        if test.get("prior_qa"):
            print(f"  (With conversation history — testing follow-up resolution)")
        print(f"{'─' * 70}")

        result = run_test(test)
        results.append(result)

        if result["status"] == "PASS":
            passed += 1
            print(f"\n  SQL GENERATED:")
            print(f"  {result.get('sql', 'N/A')[:300]}")
            print(f"\n  ROWS RETURNED: {result.get('row_count', 0)}")
            if result.get("rows"):
                print(format_results_as_table(result["rows"], max_rows=5))
            print(f"\n  ANSWER:")
            # Indent the answer
            for line in result["answer"].split("\n"):
                print(f"  {line}")
            print(f"\n  Chart: {result['chart_type']}  |  Time: {result['elapsed_secs']}s  |  ✅ PASS")

        elif result["status"] == "FAIL":
            failed += 1
            print(f"\n  ❌ FAILED: {result.get('error', 'Unknown error')}")
            if result.get("sql"):
                print(f"\n  SQL attempted:\n  {result.get('sql', '')[:300]}")

        elif result["status"] == "SKIP":
            print(f"\n  ⚠️  SKIPPED: {result['message']}")

    # ── Summary ───────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("  CHAIN TEST SUMMARY")
    print("=" * 70)
    print(f"\n  Tests run    : {len(TEST_QUESTIONS)}")
    print(f"  Passed       : {passed}  ✅")
    print(f"  Failed       : {failed}  {'❌' if failed > 0 else '✅'}")

    if failed == 0:
        print("\n  ✅  ALL TESTS PASSED — Phase 2C COMPLETE")
        print("  The full Text-to-SQL chain is working end-to-end.")
        print("\n  NEXT STEPS:")
        print("  Phase 3 — Semantic Layer + Multi-turn Conversation Engine")
        print("  Phase 4 — Streamlit UI → GitHub → Streamlit Cloud deploy")
    else:
        print(f"\n  ❌  {failed} test(s) failed. Fix before moving to Phase 3.")
        print("\n  COMMON FIXES:")
        print("  SQL FAIL → Check schema_context.txt — column name may be wrong")
        print("  Athena FAIL → Run the SQL manually in Athena Console to see the error")
        print("  Answer FAIL → Check Bedrock permissions / model enabled")

    print()
    return failed


if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)
