"""
IPL Analytics Project — Script 4: Data Cleaning
-------------------------------------------------
PURPOSE : Some venue names contain commas (e.g. "Wankhede Stadium, Mumbai").
          When those rows were combined into one CSV, the unquoted comma
          caused every column in that row to shift right by one position.
          Result: team names like "Kolkata Knight Riders" end up in the
          striker column instead of a player name.

          This script detects corrupted rows (innings != 1 or 2),
          removes them, rebuilds both output files, and re-uploads to S3.

HOW TO RUN (from your ipl_project folder in VS Code terminal):
    python scripts/clean_data.py

WHAT IT DOES:
    1. Loads raw all_matches.csv
    2. Reports how many rows are corrupted
    3. Removes corrupted rows
    4. Rebuilds all_matches_info.csv from clean data
    5. Overwrites both files locally
    6. Re-uploads both files to S3
    7. Prints a final health check
"""

import os
import boto3
import pandas as pd

# ─────────────────────────────────────────────
# CONFIGURATION — must match your setup_s3.py
# ─────────────────────────────────────────────

BUCKET_NAME        = "abhishek-ipl-analytics-2026"   # ← your exact bucket name
AWS_REGION         = "us-east-1"

DELIVERIES_FILE    = os.path.join("raw_data", "all_matches.csv")
INFO_FILE          = os.path.join("raw_data", "all_matches_info.csv")

S3_DELIVERIES_KEY  = "raw/deliveries/all_matches.csv"
S3_INFO_KEY        = "raw/matches/all_matches_info.csv"

# All known IPL team names — used for secondary validation check
IPL_TEAMS = {
    "Mumbai Indians", "Chennai Super Kings", "Royal Challengers Bangalore",
    "Royal Challengers Bengaluru", "Kolkata Knight Riders", "Rajasthan Royals",
    "Delhi Capitals", "Delhi Daredevils", "Sunrisers Hyderabad",
    "Kings XI Punjab", "Punjab Kings", "Deccan Chargers", "Pune Warriors India",
    "Gujarat Lions", "Rising Pune Supergiant", "Rising Pune Supergiants",
    "Kochi Tuskers Kerala", "Gujarat Titans", "Lucknow Super Giants"
}

# ─────────────────────────────────────────────
# STEP 1 : Load data
# ─────────────────────────────────────────────

print("=" * 60)
print("  IPL DATA CLEANER")
print("=" * 60)

print(f"\nLoading {DELIVERIES_FILE} ...")
deliveries = pd.read_csv(DELIVERIES_FILE, low_memory=False)
print(f"✅  Loaded {len(deliveries):,} rows  |  {len(deliveries.columns)} columns")

# ─────────────────────────────────────────────
# STEP 2 : Detect corrupted rows
#
# In a valid row: innings is 1 or 2 (or 1.0 / 2.0 as float)
# In a corrupted row (venue had comma): innings contains a city
# name like " Mumbai" or " Jaipur" — clearly not 1 or 2
# ─────────────────────────────────────────────

print("\nDetecting corrupted rows ...")

# Normalize innings to string for comparison
innings_str = deliveries["innings"].astype(str).str.strip()
valid_mask  = innings_str.isin(["1", "2", "1.0", "2.0"])

corrupted   = deliveries[~valid_mask]
clean       = deliveries[valid_mask].copy()

print(f"\n  Total rows          : {len(deliveries):,}")
print(f"  Valid rows          : {len(clean):,}")
print(f"  Corrupted rows      : {len(corrupted):,}  "
      f"({100 * len(corrupted) / len(deliveries):.1f}% of data)")

if len(corrupted) > 0:
    print(f"\n  Sample corrupted innings values:")
    sample_bad = corrupted["innings"].astype(str).unique()[:8]
    for val in sample_bad:
        print(f"    innings = '{val}'")

# Secondary check: any team names still in striker after innings filter?
if "striker" in clean.columns:
    striker_is_team = clean["striker"].isin(IPL_TEAMS)
    team_in_striker = clean[striker_is_team]
    if len(team_in_striker) > 0:
        print(f"\n  ⚠️  {len(team_in_striker):,} rows still have team names in striker "
              f"after innings filter — removing those too.")
        clean = clean[~striker_is_team]
    else:
        print(f"\n  ✅  No team names remain in striker after innings filter.")

print(f"\n  Final clean row count: {len(clean):,}")

# ─────────────────────────────────────────────
# STEP 3 : Reset innings to integer (1 or 2)
# ─────────────────────────────────────────────

clean["innings"] = clean["innings"].astype(float).astype(int)

# ─────────────────────────────────────────────
# STEP 4 : Rebuild match info from clean deliveries
# ─────────────────────────────────────────────

print("\nRebuilding match info from clean data ...")

INFO_COLS     = ["match_id", "season", "start_date", "venue",
                 "batting_team", "bowling_team"]
cols_present  = [c for c in INFO_COLS if c in clean.columns]

# Take innings 1 rows, one row per match
inn1     = clean[clean["innings"] == 1]
info_new = (inn1[cols_present]
            .drop_duplicates(subset=["match_id"])
            .rename(columns={"batting_team": "team1",
                             "bowling_team": "team2"})
            .sort_values("match_id")
            .reset_index(drop=True))

# ─────────────────────────────────────────────
# FIX: Venue names with commas (e.g. "Wankhede Stadium, Mumbai")
#
# ROOT CAUSE: Athena uses a simple CSV parser that does NOT respect
# standard CSV quoting. When pandas writes a venue like
# "Wankhede Stadium, Mumbai" with proper CSV quoting, Athena ignores
# the quotes and splits on the comma — shifting every column after
# venue one position right, so batting_team ends up in striker.
#
# FIX: Replace commas in venue with " -" so no column shifting occurs.
# "Wankhede Stadium, Mumbai" → "Wankhede Stadium - Mumbai"
# ─────────────────────────────────────────────
if "venue" in clean.columns:
    # Strip leading quote artifact first
    clean["venue"]    = clean["venue"].astype(str).str.lstrip('"').str.strip()
    info_new["venue"] = info_new["venue"].astype(str).str.lstrip('"').str.strip()

    # Count venues that have commas before fixing
    venues_with_comma = clean["venue"].str.contains(",", regex=False).sum()
    print(f"\nVenue comma fix: {venues_with_comma:,} delivery rows had commas in venue name")

    # Replace comma with " -" in both files
    clean["venue"]    = clean["venue"].str.replace(",", " -", regex=False)
    info_new["venue"] = info_new["venue"].str.replace(",", " -", regex=False)

    # Show sample of fixed venue names
    fixed_venues = clean[clean["venue"].str.contains(" - ", regex=False)]["venue"].unique()[:5]
    if len(fixed_venues) > 0:
        print("Sample fixed venue names:")
        for v in fixed_venues:
            print(f"  {v}")

print(f"✅  Match info rows    : {len(info_new):,}")
print(f"    Seasons            : {sorted(clean['season'].unique())}")

# ─────────────────────────────────────────────
# STEP 5 : Save cleaned files locally
# ─────────────────────────────────────────────

print(f"\nSaving cleaned files ...")

clean.to_csv(DELIVERIES_FILE, index=False)
size_mb = os.path.getsize(DELIVERIES_FILE) / (1024 * 1024)
print(f"✅  {DELIVERIES_FILE}  ({size_mb:.1f} MB)")

info_new.to_csv(INFO_FILE, index=False)
size_kb = os.path.getsize(INFO_FILE) / 1024
print(f"✅  {INFO_FILE}  ({size_kb:.1f} KB)")

# ─────────────────────────────────────────────
# STEP 6 : Re-upload both files to S3
# ─────────────────────────────────────────────

print(f"\nRe-uploading cleaned files to S3 ...")

try:
    s3 = boto3.client("s3", region_name=AWS_REGION)
    s3.head_bucket(Bucket=BUCKET_NAME)
    print(f"✅  Connected to S3 bucket: {BUCKET_NAME}")
except Exception as e:
    print(f"❌  S3 connection failed: {e}")
    print("    Files saved locally. Re-upload manually with upload_to_s3.py")
    exit(1)

uploads = [
    (DELIVERIES_FILE, S3_DELIVERIES_KEY, "Deliveries CSV"),
    (INFO_FILE,       S3_INFO_KEY,       "Match Info CSV"),
]

for local_path, s3_key, label in uploads:
    print(f"\n  Uploading {label} ...")
    s3.upload_file(Filename=local_path, Bucket=BUCKET_NAME, Key=s3_key)
    obj      = s3.head_object(Bucket=BUCKET_NAME, Key=s3_key)
    s3_size  = obj["ContentLength"] / (1024 * 1024)
    print(f"  ✅  s3://{BUCKET_NAME}/{s3_key}  ({s3_size:.1f} MB)")

# ─────────────────────────────────────────────
# STEP 7 : Final health check
# ─────────────────────────────────────────────

print("\n" + "=" * 60)
print("  FINAL HEALTH CHECK")
print("=" * 60)

# Verify no team names remain in striker
if "striker" in clean.columns:
    remaining = clean["striker"].isin(IPL_TEAMS).sum()
    if remaining == 0:
        print(f"\n  ✅  striker column — no team names present")
    else:
        print(f"\n  ⚠️  {remaining} team names still in striker — investigate manually")

# Innings check
innings_vals = clean["innings"].unique()
print(f"  ✅  innings values   — {sorted(innings_vals)}  (expected: [1, 2])")

# Season check
seasons = sorted(clean["season"].unique())
print(f"  ✅  seasons          — {seasons}")

# Top 5 strikers (should be player names only)
top5 = (clean.groupby("striker")["runs_off_bat"]
        .sum()
        .sort_values(ascending=False)
        .head(5)
        .reset_index())
top5.columns = ["Batter", "Total Runs"]
print(f"\n  Top 5 run scorers (should all be player names):")
print(top5.to_string(index=False))

rows_removed   = len(deliveries) - len(clean)
rows_retained  = len(clean)
unique_matches = clean['match_id'].nunique()

print("\n  SUMMARY")
print("  " + "─" * 50)
print(f"  Rows removed (corrupted)  : {rows_removed:,}")
print(f"  Rows retained (clean)     : {rows_retained:,}")
print(f"  Unique matches            : {unique_matches:,}")
print(f"  Venue commas fixed        : Yes (replaced with ' - ')")
print(f"  S3 re-upload              : Complete")
print("  " + "─" * 50)
print("\n  Next steps in AWS Console:")
print("  1. Glue → Crawlers → ipl_data_crawler → Run")
print("  2. Athena → SELECT DISTINCT striker FROM deliveries LIMIT 50")
print("     Should show only player names — no team names")
print()
