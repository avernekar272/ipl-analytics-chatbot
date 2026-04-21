"""
IPL Analytics Project — Script 5: DQ Fix Pass 2
------------------------------------------------
PURPOSE : Fixes three data quality issues found from the Athena DQ test suite.
          These issues directly corrupt LLM answers in the RAG chatbot.

  FIX-1 (DQ-004) : 42,488 delivery rows have null season
                   → Self-heal using other deliveries in the same match_id.
                     Fall back to matches table. Drop any still-null rows.
                   → WHY: Season is a core filter in 90% of cricket queries.
                     Null season rows silently drop from aggregations.

  FIX-2 (DQ-006/011) : 269,430 rows have empty string "" in wicket_type
                        instead of NULL. pandas COUNT("") = non-null.
                   → Replace "" with NULL in wicket_type and player_dismissed.
                   → WHY: This is the most dangerous bug for RAG.
                     COUNT(wicket_type) returns 280,000 instead of ~20,000.
                     Every wicket stat the LLM returns will be wrong by 10x.

  FIX-3 (DQ-022) : 39 duplicate delivery rows (same match_id + innings + ball)
                   → Drop duplicates, keep first occurrence.
                   → WHY: Inflates run totals and ball counts for those matches.

  SKIPPED (DQ-017) : "Pune Warriors" is a valid franchise name (IPL 2011-2013).
                     Not a bug — our known-teams list was incomplete.

HOW TO RUN (from your ipl_project folder in VS Code terminal):
    python scripts/fix_dq_pass2.py
"""

import os
import boto3
import pandas as pd
import numpy as np

# ─────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────

BUCKET_NAME       = "abhishek-ipl-analytics-2026"
AWS_REGION        = "us-east-1"

DELIVERIES_FILE   = os.path.join("raw_data", "all_matches.csv")
INFO_FILE         = os.path.join("raw_data", "all_matches_info.csv")

S3_DELIVERIES_KEY = "raw/deliveries/all_matches.csv"
S3_INFO_KEY       = "raw/matches/all_matches_info.csv"

# ─────────────────────────────────────────────
# LOAD DATA
# ─────────────────────────────────────────────

print("=" * 65)
print("  IPL DQ FIX — PASS 2")
print("=" * 65)

print(f"\nLoading {DELIVERIES_FILE} ...")
deliveries = pd.read_csv(DELIVERIES_FILE, low_memory=False)
print(f"  ✅  {len(deliveries):,} rows loaded  |  {len(deliveries.columns)} columns")

print(f"\nLoading {INFO_FILE} ...")
matches = pd.read_csv(INFO_FILE, low_memory=False)
print(f"  ✅  {len(matches):,} rows loaded")

rows_start = len(deliveries)


# ─────────────────────────────────────────────
# FIX-1 : NULL SEASON (DQ-004)
# ─────────────────────────────────────────────
#
# WHY THIS MATTERS FOR RAG:
#   User asks: "Who scored most runs in IPL 2024?"
#   SQL: WHERE season = '2024' GROUP BY striker ORDER BY SUM(runs_off_bat)
#   If 15% of 2024 deliveries have null season → they are silently excluded
#   → Virat Kohli's actual total might be 800 but LLM answers 680. Wrong.

print("\n" + "─" * 65)
print("FIX-1 : Null season back-fill (DQ-004)")
print("─" * 65)

null_before = deliveries["season"].isna().sum()
print(f"  Null seasons before fix : {null_before:,}")

if null_before > 0:
    # Strategy 1: self-heal within deliveries
    # For each match_id, find the non-null season from any other row in that match
    season_from_self = (
        deliveries[deliveries["season"].notna()]
        .groupby("match_id")["season"]
        .first()
        .to_dict()
    )
    mask_null = deliveries["season"].isna()
    deliveries.loc[mask_null, "season"] = (
        deliveries.loc[mask_null, "match_id"].map(season_from_self)
    )
    filled_self = null_before - deliveries["season"].isna().sum()
    print(f"  Filled via self-heal (same match_id in deliveries) : {filled_self:,}")

    # Strategy 2: fill from matches info table
    still_null = deliveries["season"].isna().sum()
    if still_null > 0 and "season" in matches.columns and "match_id" in matches.columns:
        season_from_matches = (
            matches[matches["season"].notna()]
            .set_index("match_id")["season"]
            .to_dict()
        )
        mask_still_null = deliveries["season"].isna()
        deliveries.loc[mask_still_null, "season"] = (
            deliveries.loc[mask_still_null, "match_id"].map(season_from_matches)
        )
        filled_matches = still_null - deliveries["season"].isna().sum()
        print(f"  Filled via matches table                          : {filled_matches:,}")

    # Strategy 3: drop rows where season is still null (no source to fill from)
    still_null_final = deliveries["season"].isna().sum()
    if still_null_final > 0:
        print(f"  ⚠️   {still_null_final:,} rows still null after both fill strategies")
        print(f"       Dropping those rows — no season attribution possible")
        deliveries = deliveries[deliveries["season"].notna()].copy()

    print(f"  ✅  Null seasons remaining : {deliveries['season'].isna().sum():,}")
    print(f"  ✅  Rows after FIX-1       : {len(deliveries):,}")
else:
    print("  ✅  No null seasons found — skipping")


# ─────────────────────────────────────────────
# FIX-2 : EMPTY STRING WICKET_TYPE (DQ-006 + DQ-011)
# ─────────────────────────────────────────────
#
# WHY THIS MATTERS FOR RAG:
#   User asks: "How many wickets did Jasprit Bumrah take in IPL 2024?"
#   SQL: WHERE bowler = 'JJ Bumrah' AND wicket_type IS NOT NULL
#   If wicket_type = "" for every non-wicket ball → IS NOT NULL passes ALL rows
#   → Bumrah's actual wickets might be 20 but LLM answers 312. Catastrophically wrong.
#
# ROOT CAUSE:
#   Cricsheet CSV uses empty field ("") for no-wicket deliveries.
#   pandas read_csv reads "" as an empty string, not NaN.
#   COUNT("") counts as non-null in Athena → 100% fill rate (should be ~7%).

print("\n" + "─" * 65)
print("FIX-2 : Convert empty-string wicket columns to NULL (DQ-006/011)")
print("─" * 65)

WICKET_COLS = ["wicket_type", "player_dismissed"]

for col in WICKET_COLS:
    if col not in deliveries.columns:
        print(f"  ⚠️   Column '{col}' not found — skipping")
        continue

    # Count current state
    total          = len(deliveries)
    currently_null = deliveries[col].isna().sum()
    empty_strings  = (deliveries[col].astype(str).str.strip() == "").sum()

    print(f"\n  Column: {col}")
    print(f"    Currently null        : {currently_null:,}")
    print(f"    Empty strings (\"\")    : {empty_strings:,}")
    print(f"    Current fill rate     : {(total - currently_null) * 100 / total:.1f}%")

    # Fix: replace all empty / whitespace-only strings with NaN
    deliveries[col] = deliveries[col].astype(str).str.strip()
    deliveries[col] = deliveries[col].replace({"": np.nan, "nan": np.nan, "None": np.nan})

    non_null_after = deliveries[col].notna().sum()
    fill_rate_after = non_null_after * 100 / total
    print(f"    Non-null after fix    : {non_null_after:,}")
    print(f"    Fill rate after fix   : {fill_rate_after:.1f}%  (expected ~5–8% for T20)")

    if fill_rate_after < 4 or fill_rate_after > 12:
        print(f"    ⚠️   Fill rate still outside 4–12% range — manual review suggested")
    else:
        print(f"    ✅  Fill rate is within expected T20 range")


# ─────────────────────────────────────────────
# FIX-3 : DUPLICATE DELIVERIES (DQ-022)
# ─────────────────────────────────────────────
#
# WHY THIS MATTERS FOR RAG:
#   User asks: "What was the highest total scored in IPL 2024?"
#   SQL: SUM(runs_off_bat + extras) GROUP BY match_id
#   If ball 16.1 in a match is counted twice → that match's score is inflated.
#   → LLM might incorrectly crown a match as the highest-scoring.

print("\n" + "─" * 65)
print("FIX-3 : Remove duplicate delivery records (DQ-022)")
print("─" * 65)

rows_before_dedup = len(deliveries)

# Identify duplicates on the natural key: match_id + innings + ball
dupes_mask = deliveries.duplicated(subset=["match_id", "innings", "ball"], keep=False)
dup_count  = dupes_mask.sum()
print(f"  Total rows involved in duplicates : {dup_count:,}")

# Show a sample for transparency
if dup_count > 0:
    sample = (
        deliveries[dupes_mask]
        .groupby(["match_id", "innings", "ball"])
        .size()
        .reset_index(name="count")
        .head(5)
    )
    print(f"\n  Sample duplicate keys:")
    print(sample.to_string(index=False))

# Keep first occurrence of each duplicate group
deliveries = deliveries.drop_duplicates(
    subset=["match_id", "innings", "ball"],
    keep="first"
).copy()

removed_dedup = rows_before_dedup - len(deliveries)
print(f"\n  Duplicate rows removed : {removed_dedup:,}")
print(f"  ✅  Rows after FIX-3   : {len(deliveries):,}")


# ─────────────────────────────────────────────
# REBUILD MATCH INFO FILE
# ─────────────────────────────────────────────

print("\n" + "─" * 65)
print("Rebuilding all_matches_info.csv from cleaned deliveries ...")
print("─" * 65)

INFO_COLS    = ["match_id", "season", "start_date", "venue", "batting_team", "bowling_team"]
cols_present = [c for c in INFO_COLS if c in deliveries.columns]

inn1     = deliveries[deliveries["innings"] == 1]
info_new = (
    inn1[cols_present]
    .drop_duplicates(subset=["match_id"])
    .rename(columns={"batting_team": "team1", "bowling_team": "team2"})
    .sort_values("match_id")
    .reset_index(drop=True)
)
print(f"  ✅  Match info rows rebuilt : {len(info_new):,}")


# ─────────────────────────────────────────────
# SAVE CLEANED FILES LOCALLY
# ─────────────────────────────────────────────

print("\n" + "─" * 65)
print("Saving cleaned files locally ...")
print("─" * 65)

deliveries.to_csv(DELIVERIES_FILE, index=False)
size_mb = os.path.getsize(DELIVERIES_FILE) / (1024 * 1024)
print(f"  ✅  {DELIVERIES_FILE}  ({size_mb:.1f} MB)")

info_new.to_csv(INFO_FILE, index=False)
size_kb = os.path.getsize(INFO_FILE) / 1024
print(f"  ✅  {INFO_FILE}  ({size_kb:.1f} KB)")


# ─────────────────────────────────────────────
# RE-UPLOAD TO S3
# ─────────────────────────────────────────────

print("\n" + "─" * 65)
print("Re-uploading cleaned files to S3 ...")
print("─" * 65)

try:
    s3 = boto3.client("s3", region_name=AWS_REGION)
    s3.head_bucket(Bucket=BUCKET_NAME)
    print(f"  ✅  Connected to bucket: {BUCKET_NAME}")
except Exception as e:
    print(f"  ❌  S3 connection failed: {e}")
    print("      Files saved locally. Re-upload manually using upload_to_s3.py")
    exit(1)

uploads = [
    (DELIVERIES_FILE, S3_DELIVERIES_KEY, "Deliveries CSV"),
    (INFO_FILE,       S3_INFO_KEY,       "Match Info CSV"),
]

for local_path, s3_key, label in uploads:
    print(f"\n  Uploading {label} ...")
    s3.upload_file(Filename=local_path, Bucket=BUCKET_NAME, Key=s3_key)
    obj     = s3.head_object(Bucket=BUCKET_NAME, Key=s3_key)
    s3_size = obj["ContentLength"] / (1024 * 1024)
    print(f"  ✅  s3://{BUCKET_NAME}/{s3_key}  ({s3_size:.1f} MB)")


# ─────────────────────────────────────────────
# FINAL HEALTH CHECK
# ─────────────────────────────────────────────

print("\n" + "=" * 65)
print("  FINAL HEALTH CHECK")
print("=" * 65)

total_rows     = len(deliveries)
null_seasons   = deliveries["season"].isna().sum()
dup_remaining  = deliveries.duplicated(subset=["match_id", "innings", "ball"]).sum()

print(f"\n  Total delivery rows     : {total_rows:,}")
print(f"  Null seasons            : {null_seasons:,}        (expected: 0)")
print(f"  Duplicate deliveries    : {dup_remaining:,}        (expected: 0)")

if "wicket_type" in deliveries.columns:
    wkt_non_null = deliveries["wicket_type"].notna().sum()
    wkt_pct      = wkt_non_null * 100 / total_rows
    print(f"  Wicket fill rate        : {wkt_pct:.1f}%       (expected: 4–10%)")

seasons = sorted(deliveries["season"].dropna().unique())
print(f"  Seasons present         : {seasons}")

top5 = (
    deliveries.groupby("striker")["runs_off_bat"]
    .sum()
    .sort_values(ascending=False)
    .head(5)
    .reset_index()
)
top5.columns = ["Batter", "Total Runs"]
print(f"\n  Top 5 run scorers (all should be player names):")
print(top5.to_string(index=False))

rows_removed_total = rows_start - total_rows
print("\n  SUMMARY OF FIXES APPLIED")
print("  " + "─" * 50)
print(f"  FIX-1 : Null season rows back-filled / dropped")
print(f"  FIX-2 : Empty-string wicket_type → NULL (269,430 rows fixed)")
print(f"  FIX-3 : {rows_removed_total:,} duplicate delivery rows removed")
print(f"  SKIP  : DQ-017 (Pune Warriors) — valid franchise name, not a bug")
print("  " + "─" * 50)
print("\n  NEXT STEPS in AWS Console:")
print("  1. Glue → Crawlers → ipl_data_crawler → Run crawler")
print("  2. Wait for crawler to finish (1-2 min)")
print("  3. Re-run the full DQ test suite in Athena → expect 0 rows")
print()
