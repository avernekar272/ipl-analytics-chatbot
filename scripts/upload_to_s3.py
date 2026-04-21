"""
IPL Analytics Project — Script 3 of 3
--------------------------------------
PURPOSE : Uploads both IPL CSV files from your local raw_data/ folder
          into the S3 bucket, then verifies the upload was successful.

HOW TO RUN (from your ipl_project folder in the VS Code terminal):
    python scripts/upload_to_s3.py

PRE-REQUISITES:
    - setup_s3.py has been run successfully
    - Both CSV files are present in raw_data/
    - AWS CLI is configured (aws configure)

WHAT THIS SCRIPT DOES:
    1. Uploads all_matches_info.csv  →  s3://bucket/raw/matches/
    2. Uploads all_matches.csv       →  s3://bucket/raw/deliveries/
    3. Shows upload progress with file sizes
    4. Verifies both files are accessible in S3
    5. Prints a summary with the exact S3 paths
"""

import os
import boto3
from botocore.exceptions import ClientError

# ─────────────────────────────────────────────
# CONFIGURATION — must match setup_s3.py
# ─────────────────────────────────────────────

BUCKET_NAME     = "abhishek-ipl-analytics-2026"   # ← same name you used in setup_s3.py
AWS_REGION      = "us-east-1"

MATCHES_LOCAL   = os.path.join("raw_data", "all_matches_info.csv")
DELIVERIES_LOCAL= os.path.join("raw_data", "all_matches.csv")

MATCHES_S3_KEY  = "raw/matches/all_matches_info.csv"
DELIVERIES_S3_KEY = "raw/deliveries/all_matches.csv"

# ─────────────────────────────────────────────
# HELPER : human-readable file size
# ─────────────────────────────────────────────

def human_size(num_bytes):
    for unit in ["B", "KB", "MB", "GB"]:
        if num_bytes < 1024:
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024
    return f"{num_bytes:.1f} TB"

# ─────────────────────────────────────────────
# STEP 1 : Pre-flight checks
# ─────────────────────────────────────────────

print("=" * 60)
print("  IPL DATA UPLOAD TO S3")
print("=" * 60)

# Check local files exist
all_ok = True
for filepath in [MATCHES_LOCAL, DELIVERIES_LOCAL]:
    if os.path.exists(filepath):
        size = human_size(os.path.getsize(filepath))
        print(f"\n✅  Found: {filepath}  ({size})")
    else:
        print(f"\n❌  Missing: {filepath}")
        print(f"    Make sure you extracted the Cricsheet ZIP into raw_data/")
        all_ok = False

if not all_ok:
    print("\nFix the missing files above, then re-run this script.")
    exit(1)

# Connect to S3
print("\nConnecting to AWS S3 ...")
try:
    s3 = boto3.client("s3", region_name=AWS_REGION)
    s3.head_bucket(Bucket=BUCKET_NAME)
    print(f"✅  Connected. Bucket '{BUCKET_NAME}' found.")
except ClientError as e:
    err = e.response["Error"]["Code"]
    if err == "404":
        print(f"\n❌  Bucket '{BUCKET_NAME}' does not exist.")
        print("    Run setup_s3.py first, then come back to this script.")
    else:
        print(f"\n❌  AWS connection error: {e}")
    exit(1)

# ─────────────────────────────────────────────
# STEP 2 : Upload files with progress callback
# ─────────────────────────────────────────────

class ProgressPrinter:
    """Prints upload % as each chunk is transferred."""
    def __init__(self, filename):
        self._filename   = filename
        self._total_size = float(os.path.getsize(filename))
        self._uploaded   = 0

    def __call__(self, bytes_amount):
        self._uploaded += bytes_amount
        pct = (self._uploaded / self._total_size) * 100
        print(f"\r    Uploading ... {pct:.1f}%  ({human_size(self._uploaded)} / {human_size(self._total_size)})", end="")
        if self._uploaded >= self._total_size:
            print()  # newline when done

uploads = [
    (MATCHES_LOCAL,    MATCHES_S3_KEY,    "Match Info CSV"),
    (DELIVERIES_LOCAL, DELIVERIES_S3_KEY, "Deliveries CSV"),
]

print()
for local_path, s3_key, label in uploads:
    print(f"Uploading {label} ...")
    print(f"  From : {local_path}")
    print(f"  To   : s3://{BUCKET_NAME}/{s3_key}")
    try:
        s3.upload_file(
            Filename=local_path,
            Bucket=BUCKET_NAME,
            Key=s3_key,
            Callback=ProgressPrinter(local_path)
        )
        print(f"✅  Upload complete: {label}\n")
    except Exception as e:
        print(f"\n❌  Upload failed for {label}: {e}")
        exit(1)

# ─────────────────────────────────────────────
# STEP 3 : Verify — confirm files exist in S3
# ─────────────────────────────────────────────

print("Verifying uploads in S3 ...")
print()

all_verified = True
for local_path, s3_key, label in uploads:
    try:
        obj = s3.head_object(Bucket=BUCKET_NAME, Key=s3_key)
        s3_size   = human_size(obj["ContentLength"])
        local_size= human_size(os.path.getsize(local_path))
        print(f"  ✅  {label}")
        print(f"      S3 path   : s3://{BUCKET_NAME}/{s3_key}")
        print(f"      S3 size   : {s3_size}  |  Local size: {local_size}")
        print()
    except ClientError:
        print(f"  ❌  {label} — NOT found in S3. Upload may have failed.")
        all_verified = False

# ─────────────────────────────────────────────
# STEP 4 : Final summary
# ─────────────────────────────────────────────

print("=" * 60)
if all_verified:
    print("  UPLOAD COMPLETE — ALL FILES VERIFIED IN S3")
    print("=" * 60)
    print(f"""
  Bucket      : s3://{BUCKET_NAME}/
  Matches     : s3://{BUCKET_NAME}/{MATCHES_S3_KEY}
  Deliveries  : s3://{BUCKET_NAME}/{DELIVERIES_S3_KEY}

  You can verify in the AWS Console:
    https://s3.console.aws.amazon.com/s3/buckets/{BUCKET_NAME}

  Next steps:
    1. Go to AWS Console → Glue → Create Database → ipl_analytics_db
    2. Create a Glue Crawler pointing at s3://{BUCKET_NAME}/raw/
    3. Run the crawler — it auto-creates the 'matches' and 'deliveries' tables
    4. Open Athena and run your first SQL query
""")
else:
    print("  SOME UPLOADS FAILED — CHECK ERRORS ABOVE")
    print("=" * 60)
