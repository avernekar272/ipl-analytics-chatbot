"""
IPL Analytics Project — Script 1 of 3
--------------------------------------
PURPOSE : Loads both Cricsheet CSV files and prints a full profile
          of the data so you know exactly what columns you have and
          what they contain before touching AWS.

HOW TO RUN (from your ipl_project folder in the VS Code terminal):
    python scripts/explore_data.py

EXPECTED OUTPUT:
    - Row counts for both files
    - All column names
    - Sample rows
    - Seasons available in the data
    - Top 5 batters by runs (quick sanity check)
"""

import os
import pandas as pd

# ─────────────────────────────────────────────
# SECTION 1 : Load the two CSV files
# ─────────────────────────────────────────────

# os.path.join builds a file path that works on both Windows and Mac
MATCHES_FILE    = os.path.join("raw_data", "all_matches_info.csv")
DELIVERIES_FILE = os.path.join("raw_data", "all_matches.csv")

print("=" * 60)
print("  IPL DATA EXPLORER")
print("=" * 60)

# Check files exist before trying to load them
for filepath in [MATCHES_FILE, DELIVERIES_FILE]:
    if not os.path.exists(filepath):
        print(f"\n❌  FILE NOT FOUND: {filepath}")
        print("    Make sure you extracted the Cricsheet ZIP into raw_data/")
        print("    and that the file names match exactly.\n")
        exit(1)

print("\n✅  Both data files found. Loading ...\n")

matches    = pd.read_csv(MATCHES_FILE)
deliveries = pd.read_csv(DELIVERIES_FILE)

print(f"✅  Loaded matches    : {len(matches):,} rows")
print(f"✅  Loaded deliveries : {len(deliveries):,} rows")

# ─────────────────────────────────────────────
# SECTION 2 : Match Info file profile
# ─────────────────────────────────────────────

print("\n" + "=" * 60)
print("  MATCH INFO FILE  (all_matches_info.csv)")
print("=" * 60)

print(f"\nTotal columns : {len(matches.columns)}")
print("\nColumn names and data types:")
print("-" * 40)
for col in matches.columns:
    print(f"  {col:<30} {str(matches[col].dtype):<10}  "
          f"({matches[col].notna().sum()} non-null values)")

print("\nFirst 3 rows of match data:")
print(matches.head(3).to_string(index=False))

# ─────────────────────────────────────────────
# SECTION 3 : Deliveries (ball-by-ball) file profile
# ─────────────────────────────────────────────

print("\n" + "=" * 60)
print("  DELIVERIES FILE  (all_matches.csv)")
print("=" * 60)

print(f"\nTotal columns : {len(deliveries.columns)}")
print("\nColumn names and data types:")
print("-" * 40)
for col in deliveries.columns:
    print(f"  {col:<30} {str(deliveries[col].dtype):<10}  "
          f"({deliveries[col].notna().sum()} non-null values)")

print("\nFirst 3 rows of delivery data:")
print(deliveries.head(3).to_string(index=False))

# ─────────────────────────────────────────────
# SECTION 4 : Seasons available
# ─────────────────────────────────────────────

print("\n" + "=" * 60)
print("  SEASONS IN THE DATASET")
print("=" * 60)

# Try 'season' column — it may be in matches or deliveries depending on Cricsheet version
if 'season' in deliveries.columns:
    seasons = sorted(deliveries['season'].dropna().unique())
    print(f"\nSeasons found ({len(seasons)} total): {seasons}")
elif 'season' in matches.columns:
    seasons = sorted(matches['season'].dropna().unique())
    print(f"\nSeasons found ({len(seasons)} total): {seasons}")
else:
    print("\n⚠️  No 'season' column found — check column names above and update this script.")

# ─────────────────────────────────────────────
# SECTION 5 : Quick sanity check — top 5 run scorers
# ─────────────────────────────────────────────

print("\n" + "=" * 60)
print("  TOP 5 ALL-TIME RUN SCORERS (sanity check)")
print("=" * 60)

# NOTE: Cricsheet new format uses 'striker' not 'batter'
# We check for both so the script works regardless of format
BATTER_COL = 'striker' if 'striker' in deliveries.columns else 'batter'

if 'runs_off_bat' in deliveries.columns and BATTER_COL in deliveries.columns:
    top5 = (deliveries
            .groupby(BATTER_COL)['runs_off_bat']
            .sum()
            .sort_values(ascending=False)
            .head(5)
            .reset_index())
    top5.columns = ['Batter', 'Total Runs']
    print()
    print(top5.to_string(index=False))
else:
    print("\n⚠️  Batter or runs_off_bat columns not found — check column names above.")

# ─────────────────────────────────────────────
# SECTION 6 : Join key check
# ─────────────────────────────────────────────

print("\n" + "=" * 60)
print("  JOIN KEY CHECK (match_id must exist in both files)")
print("=" * 60)

if 'match_id' in matches.columns and 'match_id' in deliveries.columns:
    match_ids_info  = set(matches['match_id'].unique())
    match_ids_deliv = set(deliveries['match_id'].unique())
    common          = match_ids_info & match_ids_deliv
    print(f"\n  match_id in matches file    : {len(match_ids_info):,}")
    print(f"  match_id in deliveries file : {len(match_ids_deliv):,}")
    print(f"  Common match_ids (join hits): {len(common):,}")
    if len(common) == len(match_ids_info):
        print("\n✅  JOIN WILL WORK — all match IDs align perfectly.")
    else:
        print("\n⚠️  Some match IDs don't align. Note this for debugging.")
else:
    print("\n⚠️  'match_id' column missing in one or both files.")

print("\n" + "=" * 60)
print("  EXPLORATION COMPLETE")
print("=" * 60)
print("\nNext step → run:  python scripts/setup_s3.py")
print()
