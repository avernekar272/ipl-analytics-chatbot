"""
IPL Analytics Project — Script 0 of 3  (run this BEFORE explore_data.py)
-------------------------------------------------------------------------
PURPOSE : Cricsheet downloads individual CSVs — one file per match.
          This script combines all match files into 2 consolidated files.

HOW TO RUN (from your ipl_project folder in the VS Code terminal):
    python scripts/combine_csvs.py

WHAT YOU NEED BEFORE RUNNING:
    ipl_project/raw_data/ipl_male_csv/   ← the unzipped Cricsheet folder

WHAT THIS SCRIPT PRODUCES:
    raw_data/all_matches.csv        ← all ball-by-ball deliveries
    raw_data/all_matches_info.csv   ← one row per match (metadata)
"""

import os
import glob
import pandas as pd

# ─────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────

SOURCE_FOLDER     = os.path.join("raw_data", "ipl_male_csv")
OUTPUT_DELIVERIES = os.path.join("raw_data", "all_matches.csv")
OUTPUT_INFO       = os.path.join("raw_data", "all_matches_info.csv")

# ─────────────────────────────────────────────
# STEP 1 : Validate folder and split file types
# ─────────────────────────────────────────────

print("=" * 60)
print("  CRICSHEET CSV COMBINER  (fixed)")
print("=" * 60)

if not os.path.isdir(SOURCE_FOLDER):
    print(f"\n❌  Folder not found: {SOURCE_FOLDER}")
    print("""
    Fix: Move the unzipped 'ipl_male_csv' folder into raw_data/ so it looks like:

        ipl_project/
        └── raw_data/
            └── ipl_male_csv/
                ├── 598068.csv
                ├── 598068_info.csv
                └── ...
    """)
    exit(1)

all_csv = glob.glob(os.path.join(SOURCE_FOLDER, "*.csv"))

# Cricsheet gives two file types per match:
#   598068.csv       → ball-by-ball delivery data
#   598068_info.csv  → match metadata (venue, result, toss, etc.)
info_files      = [f for f in all_csv if f.endswith("_info.csv")]
delivery_files  = [f for f in all_csv if not f.endswith("_info.csv")]

print(f"\n✅  Total CSV files found : {len(all_csv):,}")
print(f"    Delivery files        : {len(delivery_files):,}  (ball-by-ball)")
print(f"    Info files            : {len(info_files):,}  (match metadata)")

if len(delivery_files) == 0:
    print("\n❌  No delivery files found. Check the folder contents.")
    exit(1)

# ─────────────────────────────────────────────
# STEP 2 : Peek at one delivery file and one info file
# ─────────────────────────────────────────────

print(f"\n{'─'*60}")
print("Sample delivery file columns:")
sample_del = pd.read_csv(delivery_files[0], nrows=2)
print(f"  {list(sample_del.columns)}")

if info_files:
    print("\nSample info file columns:")
    sample_inf = pd.read_csv(info_files[0], nrows=5)
    print(f"  {list(sample_inf.columns)}")

# ─────────────────────────────────────────────
# STEP 3 : Combine all DELIVERY files
# ─────────────────────────────────────────────

print(f"\n{'─'*60}")
print(f"Combining {len(delivery_files):,} delivery files ...")
print(f"{'─'*60}")

delivery_frames = []
delivery_errors = []

for i, filepath in enumerate(delivery_files, 1):
    if i % 200 == 0 or i == 1 or i == len(delivery_files):
        print(f"  [{i:,} / {len(delivery_files):,}]  {os.path.basename(filepath)}", end="\r")
    try:
        df = pd.read_csv(filepath, low_memory=False)

        # match_id is already in the file — don't add it again
        # If for some reason it's missing, derive from filename
        if "match_id" not in df.columns:
            df.insert(0, "match_id", os.path.splitext(os.path.basename(filepath))[0])

        delivery_frames.append(df)
    except Exception as e:
        delivery_errors.append((filepath, str(e)))

print(f"\n\n✅  Delivery files read  : {len(delivery_frames):,}")
if delivery_errors:
    print(f"⚠️   Errors (skipped)    : {len(delivery_errors):,}")
    for f, e in delivery_errors[:3]:
        print(f"    {os.path.basename(f)} → {e}")

print("\nConcatenating delivery data ...")
deliveries = pd.concat(delivery_frames, ignore_index=True)
print(f"✅  Total delivery rows  : {len(deliveries):,}")
print(f"    Unique matches       : {deliveries['match_id'].nunique():,}")
print(f"    Columns              : {list(deliveries.columns)}")

# ─────────────────────────────────────────────
# STEP 4 : Build match info FROM DELIVERY DATA
#
# WHY WE SKIP THE _info.csv FILES:
# Cricsheet's _info.csv files use a proprietary key-value format
# (not a standard table) so pandas cannot read them directly.
# However, every column we need for analytics is already embedded
# in each delivery row: match_id, season, start_date, venue,
# batting_team, bowling_team.
# We derive the match-level info table by taking the first delivery
# of each match (innings=1) — this gives us one clean row per match.
# ─────────────────────────────────────────────

print(f"\n{'─'*60}")
print("Building match info from delivery data ...")
print("(Cricsheet _info.csv files use a non-standard format — skipping them)")
print(f"{'─'*60}")

# Columns available in the delivery data that describe the match (not the ball)
INFO_COLS = ["match_id", "season", "start_date", "venue",
             "batting_team", "bowling_team"]

cols_present = [c for c in INFO_COLS if c in deliveries.columns]
missing      = [c for c in INFO_COLS if c not in deliveries.columns]

if missing:
    print(f"  ⚠️  Columns not found (will be absent from info file): {missing}")

# Take innings 1 rows only so batting_team = team that batted first
if "innings" in deliveries.columns:
    inn1 = deliveries[deliveries["innings"] == 1]
else:
    inn1 = deliveries   # fallback if innings column missing

# One row per match — the first delivery of innings 1
info_combined = (inn1[cols_present]
                 .drop_duplicates(subset=["match_id"])
                 .rename(columns={"batting_team": "team1",
                                  "bowling_team": "team2"})
                 .sort_values("match_id")
                 .reset_index(drop=True))

print(f"✅  Match info rows built : {len(info_combined):,}  (one per match)")
print(f"    Columns              : {list(info_combined.columns)}")

# ─────────────────────────────────────────────
# STEP 5 : Save both output files
# ─────────────────────────────────────────────

print(f"\n{'─'*60}")
print("Saving output files ...")

deliveries.to_csv(OUTPUT_DELIVERIES, index=False)
size_mb = os.path.getsize(OUTPUT_DELIVERIES) / (1024 * 1024)
print(f"✅  {OUTPUT_DELIVERIES}  ({size_mb:.1f} MB)")

info_combined.to_csv(OUTPUT_INFO, index=False)
size_kb = os.path.getsize(OUTPUT_INFO) / 1024
print(f"✅  {OUTPUT_INFO}  ({size_kb:.1f} KB)")

# ─────────────────────────────────────────────
# STEP 6 : Final summary
# ─────────────────────────────────────────────

print("\n" + "=" * 60)
print("  COMBINATION COMPLETE")
print("=" * 60)

if "season" in deliveries.columns:
    seasons = sorted(deliveries["season"].dropna().unique())
    print(f"\n  Seasons found    : {seasons}")
    print(f"  Total seasons    : {len(seasons)}")

print(f"""
  OUTPUT FILES CREATED
  ┌──────────────────────────────────────────────────────┐
  │  {OUTPUT_DELIVERIES:<52} │
  │  Rows     : {len(deliveries):,:<43} │
  │  Columns  : {len(deliveries.columns):<43} │
  ├──────────────────────────────────────────────────────┤
  │  {OUTPUT_INFO:<52} │
  │  Rows     : {len(info_combined):,:<43} │
  │  Columns  : {len(info_combined.columns):<43} │
  └──────────────────────────────────────────────────────┘
""")
print("Next step → run:  python scripts/explore_data.py")
