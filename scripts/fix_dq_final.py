"""
IPL Analytics Project — DQ Fix Pass 3 (Final)
----------------------------------------------
Fixes 3 remaining defects after Pass 2. Root causes explained below.

  FIX-1 (DQ-004) : 42,483 null season rows
                   WHY PASS 2 FAILED: All deliveries for those match_ids
                   had null season — no non-null rows to borrow from.
                   NEW FIX: Infer season from start_date (year → IPL season).

  FIX-2 (DQ-006/011) : 269,392 empty-string "" in wicket_type
                        WHY PASS 2 FAILED: pandas NaN → to_csv() writes ""
                        → Athena LazySimpleSerDe reads "" as non-null empty
                        string. COUNT("") = non-null. The fix ran locally
                        but never actually changed what Athena sees.
                        NEW FIX: Write \\N (Hive null convention) in CSV.
                        Then ALTER TABLE in Athena to register \\N as NULL.

  FIX-3 (DQ-022) : Duplicates — already fixed in Pass 2. Nothing to do here.

HOW TO RUN:
    python scripts/fix_dq_final.py

IMPORTANT: After this script finishes, you MUST also:
    1. Run Glue Crawler
    2. Run two ALTER TABLE statements in Athena (printed at the end)
    Then re-run the DQ suite — expect 0 rows.
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

# IPL season mapping: calendar year → Cricsheet season label
# The inaugural 2008 season is labeled "2007/08" in Cricsheet
YEAR_TO_SEASON = {
    2008: "2007/08",
    2009: "2009",  2010: "2010",  2011: "2011",  2012: "2012",
    2013: "2013",  2014: "2014",  2015: "2015",  2016: "2016",
    2017: "2017",  2018: "2018",  2019: "2019",  2020: "2020",
    2021: "2021",  2022: "2022",  2023: "2023",  2024: "2024",
    2025: "2025",  2026: "2026",
}

def date_to_season(date_str):
    """Map a match start_date to the correct IPL season label."""
    try:
        year = pd.to_datetime(date_str).year
        return YEAR_TO_SEASON.get(year, str(year))
    except Exception:
        return None


# ─────────────────────────────────────────────
# LOAD DATA
# ─────────────────────────────────────────────

print("=" * 65)
print("  IPL DQ FIX — PASS 3 (FINAL)")
print("=" * 65)

print(f"\nLoading {DELIVERIES_FILE} ...")
deliveries = pd.read_csv(DELIVERIES_FILE, low_memory=False)
print(f"  ✅  {len(deliveries):,} rows  |  {len(deliveries.columns)} columns")

print(f"\nLoading {INFO_FILE} ...")
matches = pd.read_csv(INFO_FILE, low_memory=False)
print(f"  ✅  {len(matches):,} rows")


# ─────────────────────────────────────────────
# FIX-1 : NULL SEASON via start_date inference (DQ-004)
# ─────────────────────────────────────────────
#
# APPROACH: For every null-season delivery row, look up the match_id in the
# matches table to get start_date, then derive IPL season from the year.
# start_date is always recorded in Cricsheet even when season label is missing.

print("\n" + "─" * 65)
print("FIX-1 : Infer null seasons from start_date (DQ-004)")
print("─" * 65)

null_before = deliveries["season"].isna().sum()
print(f"  Null seasons before : {null_before:,}")

if null_before > 0:

    # ── Strategy A: use start_date from matches table ────────────────
    if "start_date" in matches.columns and "match_id" in matches.columns:
        date_map = (
            matches
            .dropna(subset=["start_date"])
            .set_index("match_id")["start_date"]
            .to_dict()
        )
        print(f"  match_id → start_date entries in matches table : {len(date_map):,}")

        mask_null = deliveries["season"].isna()
        inferred  = deliveries.loc[mask_null, "match_id"].map(date_map).apply(date_to_season)
        deliveries.loc[mask_null, "season"] = inferred

        filled_A = null_before - deliveries["season"].isna().sum()
        print(f"  Seasons inferred via matches.start_date         : {filled_A:,}")

    # ── Strategy B: use start_date from deliveries itself (if column exists) ─
    still_null = deliveries["season"].isna().sum()
    if still_null > 0 and "start_date" in deliveries.columns:
        mask_null2 = deliveries["season"].isna()
        inferred2  = deliveries.loc[mask_null2, "start_date"].apply(date_to_season)
        deliveries.loc[mask_null2, "season"] = inferred2
        filled_B   = still_null - deliveries["season"].isna().sum()
        print(f"  Seasons inferred via deliveries.start_date      : {filled_B:,}")

    # ── Strategy C: drop rows that couldn't be attributed to any season ──────
    still_null_final = deliveries["season"].isna().sum()
    if still_null_final > 0:
        print(f"  ⚠️   {still_null_final:,} rows have no date to infer season from — dropping")
        deliveries = deliveries[deliveries["season"].notna()].copy()

    print(f"  ✅  Null seasons remaining : {deliveries['season'].isna().sum():,}")
    print(f"  ✅  Rows after FIX-1       : {len(deliveries):,}")

else:
    print("  ✅  No null seasons — skipping")


# ─────────────────────────────────────────────
# FIX-2 : WICKET_TYPE EMPTY STRINGS (DQ-006 / DQ-011)
# ─────────────────────────────────────────────
#
# ROOT CAUSE OF PERSISTENCE:
#   pandas to_csv() writes NaN as "" (empty field).
#   Athena LazySimpleSerDe reads "" as an empty string, NOT NULL.
#   So wicket_type IS NOT NULL is True for every delivery — 100% fill rate.
#
# THE FIX (two-part):
#   Part A (this script): Write \N in the CSV for NaN values.
#                         Hive/Athena knows \N = NULL if we register it.
#   Part B (Athena SQL):  ALTER TABLE ... SET SERDEPROPERTIES
#                         ('serialization.null.format'='\N')
#   After both parts, wicket_type IS NULL correctly filters non-wicket balls.

print("\n" + "─" * 65)
print("FIX-2 : Wicket empty strings → Hive \\N null marker (DQ-006/011)")
print("─" * 65)
print("  Root cause: pandas NaN → CSV '' → Athena reads '' as non-null.")
print("  Fix: write \\N for nulls, then register it with Athena SerDe.\n")

WICKET_COLS = ["wicket_type", "player_dismissed"]

for col in WICKET_COLS:
    if col not in deliveries.columns:
        print(f"  ⚠️   Column '{col}' not in dataframe — skipping")
        continue

    total     = len(deliveries)
    non_null_before = deliveries[col].notna().sum()
    print(f"  {col}")
    print(f"    Non-null before fix   : {non_null_before:,} ({non_null_before * 100 / total:.1f}%)")

    # Step 1: coerce to string so we can detect all variants of "empty"
    deliveries[col] = deliveries[col].astype(str).str.strip()

    # Step 2: convert all empty representations to NaN
    deliveries[col] = deliveries[col].replace(
        {"": np.nan, "nan": np.nan, "None": np.nan, "NaN": np.nan}
    )

    non_null_after = deliveries[col].notna().sum()
    fill_pct       = non_null_after * 100 / total
    print(f"    Non-null after fix    : {non_null_after:,} ({fill_pct:.1f}%)")

    if 4 <= fill_pct <= 12:
        print(f"    ✅  Fill rate within expected T20 range (4–12%)")
    else:
        print(f"    ⚠️   Fill rate {fill_pct:.1f}% outside 4–12% — manual review suggested")
    print()

print("  NaN values will be written as \\N in the CSV (na_rep='\\\\N').")
print("  Athena will treat \\N as NULL after you run the ALTER TABLE below.")


# ─────────────────────────────────────────────
# REBUILD MATCH INFO FROM CLEAN DELIVERIES
# ─────────────────────────────────────────────

print("\n" + "─" * 65)
print("Rebuilding all_matches_info.csv ...")
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
print(f"  Seasons in info file       : {sorted(info_new['season'].dropna().unique())}")


# ─────────────────────────────────────────────
# SAVE — CRITICAL: use na_rep='\\N' so NaN → \N in CSV
# ─────────────────────────────────────────────

print("\n" + "─" * 65)
print("Saving cleaned files with \\N null marker ...")
print("─" * 65)

deliveries.to_csv(DELIVERIES_FILE, index=False, na_rep='\\N')
size_mb = os.path.getsize(DELIVERIES_FILE) / (1024 * 1024)
print(f"  ✅  {DELIVERIES_FILE}  ({size_mb:.1f} MB)")

info_new.to_csv(INFO_FILE, index=False, na_rep='\\N')
size_kb = os.path.getsize(INFO_FILE) / 1024
print(f"  ✅  {INFO_FILE}  ({size_kb:.1f} KB)")


# ─────────────────────────────────────────────
# RE-UPLOAD TO S3
# ─────────────────────────────────────────────

print("\n" + "─" * 65)
print("Re-uploading to S3 ...")
print("─" * 65)

try:
    s3 = boto3.client("s3", region_name=AWS_REGION)
    s3.head_bucket(Bucket=BUCKET_NAME)
    print(f"  ✅  Connected: {BUCKET_NAME}")
except Exception as e:
    print(f"  ❌  S3 connection failed: {e}")
    print("      Files saved locally. Re-upload manually with upload_to_s3.py")
    exit(1)

uploads = [
    (DELIVERIES_FILE, S3_DELIVERIES_KEY, "Deliveries CSV"),
    (INFO_FILE,       S3_INFO_KEY,       "Match Info CSV"),
]
for local_path, s3_key, label in uploads:
    print(f"\n  Uploading {label} ...")
    s3.upload_file(Filename=local_path, Bucket=BUCKET_NAME, Key=s3_key)
    obj = s3.head_object(Bucket=BUCKET_NAME, Key=s3_key)
    print(f"  ✅  s3://{BUCKET_NAME}/{s3_key}  ({obj['ContentLength'] / 1024 / 1024:.1f} MB)")


# ─────────────────────────────────────────────
# FINAL HEALTH CHECK (local)
# ─────────────────────────────────────────────

print("\n" + "=" * 65)
print("  LOCAL HEALTH CHECK")
print("=" * 65)

total        = len(deliveries)
null_season  = deliveries["season"].isna().sum()
dups         = deliveries.duplicated(subset=["match_id", "innings", "ball"]).sum()

wkt_col      = deliveries["wicket_type"] if "wicket_type" in deliveries.columns else pd.Series()
wkt_non_null = wkt_col.notna().sum()
wkt_pct      = wkt_non_null * 100 / total if total > 0 else 0

print(f"\n  Total delivery rows     : {total:,}")
print(f"  Null seasons            : {null_season:,}     (expected: 0)")
print(f"  Duplicate deliveries    : {dups:,}     (expected: 0)")
print(f"  Wicket fill rate        : {wkt_pct:.1f}%    (expected: 4–10%)")

top5 = (
    deliveries.groupby("striker")["runs_off_bat"]
    .sum()
    .sort_values(ascending=False)
    .head(5)
    .reset_index()
)
top5.columns = ["Batter", "Total Runs"]
print(f"\n  Top 5 run scorers (should all be player names):")
print(top5.to_string(index=False))


# ─────────────────────────────────────────────
# PRINT REQUIRED ATHENA STEPS
# ─────────────────────────────────────────────

print("\n" + "=" * 65)
print("  ACTION REQUIRED — 3 STEPS IN AWS CONSOLE")
print("=" * 65)

print("""
STEP 1 — Run Glue Crawler
   Glue → Crawlers → ipl_data_crawler → Run crawler
   Wait ~2 minutes for it to complete.

─────────────────────────────────────────────────
STEP 2 — Register \\N as NULL in Athena (copy-paste both)

   ALTER TABLE deliveries
   SET SERDEPROPERTIES ('serialization.null.format'='\\N');

   ALTER TABLE matches
   SET SERDEPROPERTIES ('serialization.null.format'='\\N');

   WHY: This tells Athena's LazySimpleSerDe that \\N in the CSV
   means NULL. Without this, wicket_type IS NULL will never fire.

─────────────────────────────────────────────────
STEP 3 — Re-run the full DQ test suite in Athena
   Expected result: 0 rows (all tests pass)
   Only DQ-017 (Pune Warriors) will remain — that is NOT a bug.
""")
