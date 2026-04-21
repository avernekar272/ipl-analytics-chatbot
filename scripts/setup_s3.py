"""
IPL Analytics Project — Script 2 of 3
--------------------------------------
PURPOSE : Creates the S3 bucket and folder structure that will
          act as your cloud data lake. Run this ONCE before uploading.

HOW TO RUN (from your ipl_project folder in the VS Code terminal):
    python scripts/setup_s3.py

PRE-REQUISITES:
    - AWS CLI installed and configured  (run: aws configure)
    - IAM user with AmazonS3FullAccess policy attached

WHAT THIS SCRIPT DOES:
    1. Creates an S3 bucket with a unique name
    2. Creates 5 logical folders inside the bucket
    3. Verifies the bucket is accessible
    4. Prints the full bucket structure

COST: Creating an S3 bucket is free. Storage costs pennies for this data size.
"""

import boto3
from botocore.exceptions import ClientError

# ─────────────────────────────────────────────
# CONFIGURATION — Edit BUCKET_NAME before running
# ─────────────────────────────────────────────

# ⚠️  IMPORTANT: S3 bucket names must be GLOBALLY unique across all of AWS.
#    If the name is taken, you will get a BucketAlreadyExists error.
#    Change "abhishek" to something unique to you — your initials, a random number, etc.
#    Example: "av-ipl-analytics-2026" or "ipl-data-lake-xyz99"
#    Rules: lowercase letters, numbers, hyphens only. No spaces. No underscores.

BUCKET_NAME = "abhishek-ipl-analytics-2026"
AWS_REGION  = "us-east-1"

# ─────────────────────────────────────────────
# STEP 1 : Connect to AWS S3
# ─────────────────────────────────────────────

print("=" * 60)
print("  S3 BUCKET SETUP")
print("=" * 60)

print(f"\nConnecting to AWS S3 in region: {AWS_REGION} ...")

try:
    s3 = boto3.client("s3", region_name=AWS_REGION)
    # Quick test — list buckets to confirm credentials work
    s3.list_buckets()
    print("✅  AWS credentials verified successfully.")
except Exception as e:
    print(f"\n❌  Could not connect to AWS: {e}")
    print("\n    Fix: Open your terminal and run  'aws configure'")
    print("    Enter your Access Key ID and Secret Access Key from the IAM CSV file.")
    exit(1)

# ─────────────────────────────────────────────
# STEP 2 : Create the S3 bucket
# ─────────────────────────────────────────────

print(f"\nCreating bucket: {BUCKET_NAME} ...")

try:
    if AWS_REGION == "us-east-1":
        # us-east-1 does NOT use a LocationConstraint — AWS quirk
        s3.create_bucket(Bucket=BUCKET_NAME)
    else:
        s3.create_bucket(
            Bucket=BUCKET_NAME,
            CreateBucketConfiguration={"LocationConstraint": AWS_REGION}
        )
    print(f"✅  Bucket created: s3://{BUCKET_NAME}/")

except ClientError as e:
    error_code = e.response["Error"]["Code"]
    if error_code == "BucketAlreadyOwnedByYou":
        print(f"✅  Bucket already exists and belongs to you — continuing.")
    elif error_code == "BucketAlreadyExists":
        print(f"\n❌  Bucket name '{BUCKET_NAME}' is taken by someone else.")
        print("    Edit BUCKET_NAME at the top of this script and try again.")
        exit(1)
    else:
        print(f"\n❌  Unexpected error: {e}")
        exit(1)

# ─────────────────────────────────────────────
# STEP 3 : Create folder structure
# ─────────────────────────────────────────────
#
# S3 has no real folders — it uses "key prefixes" that look like folders.
# We create empty placeholder objects (0 bytes) to establish the structure.
# This is standard practice for S3 data lake organisation.

print("\nCreating folder structure ...")

folders = [
    "raw/matches/",        # Source CSV: match metadata
    "raw/deliveries/",     # Source CSV: ball-by-ball data
    "processed/matches/",  # Future: cleaned Parquet files
    "processed/deliveries/",
    "athena-results/",     # Athena writes query output here
]

for folder in folders:
    s3.put_object(Bucket=BUCKET_NAME, Key=folder)
    print(f"  📁  s3://{BUCKET_NAME}/{folder}")

# ─────────────────────────────────────────────
# STEP 4 : Verify — list everything in the bucket
# ─────────────────────────────────────────────

print("\nVerifying bucket structure ...")
response = s3.list_objects_v2(Bucket=BUCKET_NAME)

if "Contents" in response:
    print(f"\n  {'Path':<45} {'Size':>10}")
    print("  " + "-" * 57)
    for obj in response["Contents"]:
        print(f"  s3://{BUCKET_NAME}/{obj['Key']:<38} {obj['Size']:>6} bytes")
else:
    print("  (bucket is empty — this is unexpected, check your AWS console)")

print("\n" + "=" * 60)
print("  BUCKET SETUP COMPLETE")
print("=" * 60)
print(f"\n  Bucket URL  : s3://{BUCKET_NAME}/")
print(f"  AWS Region  : {AWS_REGION}")
print(f"  Folders     : {len(folders)} created")
print("\nNext step → run:  python scripts/upload_to_s3.py")
print()
