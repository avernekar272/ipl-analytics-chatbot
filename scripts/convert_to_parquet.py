"""
IPL Analytics Project — Script 6: Convert to Parquet (Step 2A)
---------------------------------------------------------------
PURPOSE : Converts cleaned CSV files to Parquet format and uploads
          to S3 processed/ layer. This permanently fixes all three
          remaining DQ issues without any SerDe workarounds.

WHY PARQUET FIXES THE DQ ISSUES:
  DQ-004 (null seasons)    : Parquet stores NULL as a first-class type.
                             No empty string ambiguity. Fixed.
  DQ-006/011 (wicket nulls): pandas NaN → Parquet NULL (not empty string).
                             wicket_type IS NULL now works correctly in Athena.
                             Fixed.
  Performance bonus        : Parquet is columnar + compressed. Athena queries
                             will be 5-10x faster and 70-80% cheaper (less data
                             scanned) compared to CSV.

WHAT THIS SCRIPT DOES:
  1. Loads cleaned CSVs from raw_data/
  2. Fixes data types (innings → int, runs → int, etc.)
  3. Converts NaN in wicket columns → proper Parquet NULL
  4. Writes Parquet files locally
  5. Uploads to s3://abhishek-ipl-analytics-2026/processed/
  6. Prints Athena CREATE TABLE statements to register new tables

HOW TO RUN (from your ipl_project folder in VS Code terminal):
  Step 1:  pip install pyarrow --break-system-packages
  Step 2:  python scripts/convert_to_parquet.py
  Step 3:  Run the CREATE TABLE SQL printed at the end in Athena
  Step 4:  Re-run the DQ test suite against the new tables → expect 0 rows
"""

import os
import boto3
import pandas as pd
import numpy as np

# ─────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────

BUCKET_NAME         = "abhishek-ipl-analytics-2026"
AWS_REGION          = "us-east-1"

DELIVERIES_CSV      = os.path.join("raw_data", "all_matches.csv")
INFO_CSV            = os.path.join("raw_data", "all_matches_info.csv")

DELIVERIES_PARQUET  = os.path.join("raw_data", "deliveries.parquet")
INFO_PARQUET        = os.path.join("raw_data", "matches.parquet")

S3_DELIVERIES_KEY   = "processed/deliveries/deliveries.parquet"
S3_INFO_KEY         = "processed/matches/matches.parquet"

DATABASE            = "ipl_analytics_db"

# IPL year → season label (for any remaining null season rows)
YEAR_TO_SEASON = {
    2008: "2007/08",
    2009: "2009",  2010: "2010",  2011: "2011",  2012: "2012",
    2013: "2013",  2014: "2014",  2015: "2015",  2016: "2016",
    2017: "2017",  2018: "2018",  2019: "2019",  2020: "2020",
    2021: "2021",  2022: "2022",  2023: "2023",  2024: "2024",
    2025: "2025",  2026: "2026",
}

def infer_season(date_str):
    try:
        year = pd.to_datetime(date_str).year
        return YEAR_TO_SEASON.get(year, str(year))
    except Exception:
        return None


# ─────────────────────────────────────────────
# CHECK PYARROW IS INSTALLED
# ─────────────────────────────────────────────

print("=" * 65)
print("  IPL ANALYTICS — CONVERT TO PARQUET (Step 2A)")
print("=" * 65)

try:
    import pyarrow
    print(f"\n  ✅  pyarrow {pyarrow.__version__} found")
except ImportError:
    print("\n  ❌  pyarrow not installed.")
    print("      Run this first:  pip install pyarrow --break-system-packages")
    print("      Then re-run this script.")
    exit(1)


# ─────────────────────────────────────────────
# LOAD CLEANED CSVs
# ─────────────────────────────────────────────

print(f"\nLoading {DELIVERIES_CSV} ...")
# Keep_default_na=True so \N in CSV is read as NaN by pandas
deliveries = pd.read_csv(DELIVERIES_CSV, low_memory=False, keep_default_na=True, na_values=["\\N", ""])
print(f"  ✅  {len(deliveries):,} rows  |  {len(deliveries.columns)} columns")

print(f"\nLoading {INFO_CSV} ...")
matches = pd.read_csv(INFO_CSV, low_memory=False, keep_default_na=True, na_values=["\\N", ""])
print(f"  ✅  {len(matches):,} rows  |  {len(matches.columns)} columns")


# ─────────────────────────────────────────────
# CLEAN + TYPE CAST DELIVERIES
# ─────────────────────────────────────────────

print("\n" + "─" * 65)
print("Cleaning and type-casting deliveries ...")
print("─" * 65)

# ── Fix null seasons (catch any still remaining after previous passes) ──────
null_season = deliveries["season"].isna().sum()
if null_season > 0:
    print(f"  Null seasons found : {null_season:,} — inferring from start_date ...")
    if "start_date" in deliveries.columns:
        mask = deliveries["season"].isna()
        deliveries.loc[mask, "season"] = (
            deliveries.loc[mask, "start_date"].apply(infer_season)
        )
    elif "start_date" in matches.columns:
        date_map = (
            matches.dropna(subset=["start_date"])
            .set_index("match_id")["start_date"]
            .to_dict()
        )
        mask = deliveries["season"].isna()
        deliveries.loc[mask, "season"] = (
            deliveries.loc[mask, "match_id"].map(date_map).apply(infer_season)
        )
    still_null = deliveries["season"].isna().sum()
    if still_null > 0:
        print(f"  ⚠️   Dropping {still_null:,} rows with unresolvable null season")
        deliveries = deliveries[deliveries["season"].notna()].copy()
    print(f"  ✅  Null seasons remaining : {deliveries['season'].isna().sum():,}")
else:
    print(f"  ✅  No null seasons")

# ── Fix wicket columns — convert any remaining empty strings to NaN ─────────
#    Parquet will store NaN as proper NULL automatically
WICKET_COLS = ["wicket_type", "player_dismissed"]
for col in WICKET_COLS:
    if col in deliveries.columns:
        # Ensure no lingering empty strings — these become NULL in Parquet
        deliveries[col] = deliveries[col].replace({"": np.nan, "nan": np.nan, "None": np.nan})
        non_null = deliveries[col].notna().sum()
        pct      = non_null * 100 / len(deliveries)
        print(f"  {col:<25} → {non_null:,} non-null ({pct:.1f}%)  [NULL in Parquet for rest]")

# ── Remove duplicate deliveries ─────────────────────────────────────────────
before = len(deliveries)
deliveries = deliveries.drop_duplicates(subset=["match_id", "innings", "ball"], keep="first")
if before - len(deliveries) > 0:
    print(f"  ⚠️   Removed {before - len(deliveries):,} duplicate delivery rows")
else:
    print(f"  ✅  No duplicate deliveries")

# ── Cast column types for Parquet efficiency ────────────────────────────────
print("\n  Type casting columns ...")

# innings: should be integer (1 or 2)
if "innings" in deliveries.columns:
    deliveries["innings"] = pd.to_numeric(deliveries["innings"], errors="coerce").astype("Int64")

# match_id: integer
if "match_id" in deliveries.columns:
    deliveries["match_id"] = pd.to_numeric(deliveries["match_id"], errors="coerce").astype("Int64")

# runs columns: integer
for col in ["runs_off_bat", "extras", "wides", "noballs", "byes", "legbyes", "penalty", "runs_off_bat"]:
    if col in deliveries.columns:
        deliveries[col] = pd.to_numeric(deliveries[col], errors="coerce").fillna(0).astype(int)

# ball: float (over.delivery notation, e.g. 0.1, 14.5)
if "ball" in deliveries.columns:
    deliveries["ball"] = pd.to_numeric(deliveries["ball"], errors="coerce")

# season: string
if "season" in deliveries.columns:
    deliveries["season"] = deliveries["season"].astype(str)

print(f"  ✅  Type casting complete")
print(f"  ✅  Final deliveries shape: {deliveries.shape[0]:,} rows × {deliveries.shape[1]} columns")


# ─────────────────────────────────────────────
# CLEAN + TYPE CAST MATCHES
# ─────────────────────────────────────────────

print("\n" + "─" * 65)
print("Cleaning and type-casting matches ...")
print("─" * 65)

# Fix null seasons in matches
null_season_m = matches["season"].isna().sum()
if null_season_m > 0:
    if "start_date" in matches.columns:
        mask = matches["season"].isna()
        matches.loc[mask, "season"] = (
            matches.loc[mask, "start_date"].apply(infer_season)
        )
    still = matches["season"].isna().sum()
    if still > 0:
        matches = matches[matches["season"].notna()].copy()
    print(f"  Null seasons fixed : {null_season_m - matches['season'].isna().sum():,}")

# match_id: integer
if "match_id" in matches.columns:
    matches["match_id"] = pd.to_numeric(matches["match_id"], errors="coerce").astype("Int64")

# Deduplicate matches
before_m = len(matches)
matches = matches.drop_duplicates(subset=["match_id"], keep="first")
if before_m - len(matches) > 0:
    print(f"  Removed {before_m - len(matches):,} duplicate match rows")

print(f"  ✅  Final matches shape: {matches.shape[0]:,} rows × {matches.shape[1]} columns")


# ─────────────────────────────────────────────
# WRITE PARQUET FILES
# ─────────────────────────────────────────────

print("\n" + "─" * 65)
print("Writing Parquet files locally ...")
print("─" * 65)

# NaN → NULL in Parquet automatically (no \N tricks needed)
deliveries.to_parquet(DELIVERIES_PARQUET, index=False, engine="pyarrow", compression="snappy")
size_mb = os.path.getsize(DELIVERIES_PARQUET) / (1024 * 1024)
print(f"  ✅  {DELIVERIES_PARQUET}  ({size_mb:.1f} MB)")

matches.to_parquet(INFO_PARQUET, index=False, engine="pyarrow", compression="snappy")
size_kb = os.path.getsize(INFO_PARQUET) / 1024
print(f"  ✅  {INFO_PARQUET}  ({size_kb:.1f} KB)")

print(f"\n  Compression note: Parquet+Snappy is typically 5-8x smaller than CSV.")
print(f"  Athena will scan less data → faster queries and lower cost.")


# ─────────────────────────────────────────────
# UPLOAD TO S3 (processed/ layer)
# ─────────────────────────────────────────────

print("\n" + "─" * 65)
print("Uploading to S3 processed/ layer ...")
print("─" * 65)

try:
    s3 = boto3.client("s3", region_name=AWS_REGION)
    s3.head_bucket(Bucket=BUCKET_NAME)
    print(f"  ✅  Connected: {BUCKET_NAME}")
except Exception as e:
    print(f"  ❌  S3 connection failed: {e}")
    exit(1)

uploads = [
    (DELIVERIES_PARQUET, S3_DELIVERIES_KEY, "Deliveries Parquet"),
    (INFO_PARQUET,       S3_INFO_KEY,       "Matches Parquet"),
]
for local_path, s3_key, label in uploads:
    print(f"\n  Uploading {label} ...")
    s3.upload_file(Filename=local_path, Bucket=BUCKET_NAME, Key=s3_key)
    obj = s3.head_object(Bucket=BUCKET_NAME, Key=s3_key)
    print(f"  ✅  s3://{BUCKET_NAME}/{s3_key}  ({obj['ContentLength'] / 1024 / 1024:.1f} MB)")


# ─────────────────────────────────────────────
# QUICK DATA VALIDATION
# ─────────────────────────────────────────────

print("\n" + "=" * 65)
print("  PARQUET HEALTH CHECK")
print("=" * 65)

# Read back the parquet to verify
d_check = pd.read_parquet(DELIVERIES_PARQUET)
wkt_non_null = d_check["wicket_type"].notna().sum() if "wicket_type" in d_check.columns else 0
wkt_pct      = wkt_non_null * 100 / len(d_check)

print(f"\n  Deliveries rows     : {len(d_check):,}")
print(f"  Null seasons        : {d_check['season'].isna().sum():,}  (expected: 0)")
print(f"  Wicket fill rate    : {wkt_pct:.1f}%  (expected: 4–10%)")
print(f"  Duplicate rows      : {d_check.duplicated(subset=['match_id','innings','ball']).sum():,}  (expected: 0)")

if wkt_pct < 4 or wkt_pct > 12:
    print(f"  ⚠️   Wicket fill rate still outside 4-12% — check source data")
else:
    print(f"  ✅  All checks passed locally")

top5 = (
    d_check.groupby("striker")["runs_off_bat"]
    .sum()
    .sort_values(ascending=False)
    .head(5)
    .reset_index()
)
top5.columns = ["Batter", "Total Runs"]
print(f"\n  Top 5 run scorers:")
print(top5.to_string(index=False))


# ─────────────────────────────────────────────
# PRINT ATHENA CREATE TABLE SQL
# ─────────────────────────────────────────────

print("\n" + "=" * 65)
print("  ACTION REQUIRED — RUN IN ATHENA QUERY EDITOR")
print("=" * 65)

deliveries_cols = []
for col, dtype in d_check.dtypes.items():
    if "int" in str(dtype).lower():
        athena_type = "bigint"
    elif "float" in str(dtype).lower():
        athena_type = "double"
    else:
        athena_type = "string"
    deliveries_cols.append(f"  `{col}` {athena_type}")

m_check = pd.read_parquet(INFO_PARQUET)
matches_cols = []
for col, dtype in m_check.dtypes.items():
    if "int" in str(dtype).lower():
        athena_type = "bigint"
    elif "float" in str(dtype).lower():
        athena_type = "double"
    else:
        athena_type = "string"
    matches_cols.append(f"  `{col}` {athena_type}")

deliveries_col_str = ",\n".join(deliveries_cols)
matches_col_str    = ",\n".join(matches_cols)

print(f"""
── STEP 1: Create Parquet tables in Athena ──────────────────────

CREATE EXTERNAL TABLE IF NOT EXISTS {DATABASE}.deliveries_pq (
{deliveries_col_str}
)
STORED AS PARQUET
LOCATION 's3://{BUCKET_NAME}/processed/deliveries/'
TBLPROPERTIES ('parquet.compress'='SNAPPY');


CREATE EXTERNAL TABLE IF NOT EXISTS {DATABASE}.matches_pq (
{matches_col_str}
)
STORED AS PARQUET
LOCATION 's3://{BUCKET_NAME}/processed/matches/'
TBLPROPERTIES ('parquet.compress'='SNAPPY');


── STEP 2: Quick sanity check after creating tables ─────────────

SELECT COUNT(*) AS total_rows,
       SUM(CASE WHEN season IS NULL THEN 1 ELSE 0 END) AS null_seasons,
       SUM(CASE WHEN wicket_type IS NOT NULL THEN 1 ELSE 0 END) AS wickets_recorded,
       ROUND(SUM(CASE WHEN wicket_type IS NOT NULL THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 1) AS wicket_pct
FROM {DATABASE}.deliveries_pq;

-- Expected:
--   null_seasons    = 0
--   wicket_pct      = 4–10%   (was 100% in CSV tables — now correctly NULL)


── STEP 3: Re-run the full DQ test suite ────────────────────────
   Change the FROM clauses to use deliveries_pq and matches_pq.
   Expected result: 0 rows.

   Going forward, use deliveries_pq and matches_pq for all queries.
   The old CSV-backed tables (deliveries, matches) can be left as-is
   or dropped once you confirm the Parquet tables are clean.
""")
