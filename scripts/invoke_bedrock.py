"""
IPL Analytics Project — Script: invoke_bedrock.py  (Phase 2B)
--------------------------------------------------------------
PURPOSE : Confirms AWS Bedrock is reachable and Claude responds.
          Run this BEFORE building the full chain. If this fails,
          fix IAM / model access before proceeding.

PRE-REQUISITES:
  1. AWS Bedrock Console → Model access → Enable "Claude 3 Haiku"
     (us-east-1 region, Anthropic provider)
  2. IAM → Users → ipl-analytics-user → Add inline policy:
     {
       "Version": "2012-10-17",
       "Statement": [{
         "Effect": "Allow",
         "Action": [
           "bedrock:InvokeModel",
           "bedrock:InvokeModelWithResponseStream",
           "bedrock:ListFoundationModels"
         ],
         "Resource": "*"
       }]
     }
  3. AWS CLI credentials configured (aws configure)

HOW TO RUN:
  python scripts/invoke_bedrock.py

EXPECTED OUTPUT:
  ✅ Bedrock client created
  ✅ Available Claude models listed
  ✅ Test prompt sent
  Claude says: "The IPL (Indian Premier League) is a professional..."
  ✅ Phase 2B COMPLETE — Bedrock is live
"""

import json
import boto3

AWS_REGION  = "us-east-1"
MODEL_ID    = "us.anthropic.claude-3-5-haiku-20241022-v1:0"

print("=" * 65)
print("  IPL ANALYTICS — PHASE 2B: BEDROCK CONNECTION TEST")
print("=" * 65)

# ── Step 1: Create Bedrock Runtime client ────────────────────────
print("\n[1/4] Creating Bedrock Runtime client ...")
try:
    client = boto3.client("bedrock-runtime", region_name=AWS_REGION)
    print(f"  ✅  Client created — region: {AWS_REGION}")
except Exception as e:
    print(f"  ❌  Failed to create client: {e}")
    exit(1)

# ── Step 2: List available models (confirms IAM + region access) ─
print("\n[2/4] Checking available Claude models ...")
try:
    bedrock_mgmt = boto3.client("bedrock", region_name=AWS_REGION)
    models = bedrock_mgmt.list_foundation_models(
        byProvider="Anthropic",
        byOutputModality="TEXT"
    )["modelSummaries"]
    claude_models = [m["modelId"] for m in models if "claude" in m["modelId"].lower()]
    for m in claude_models:
        print(f"  • {m}")
    if not claude_models:
        print("  ⚠️  No Claude models found — check model access in Bedrock Console")
    else:
        print(f"  ✅  {len(claude_models)} Claude model(s) available")
except Exception as e:
    print(f"  ⚠️  Could not list models (non-fatal): {e}")

# ── Step 3: Send a simple test prompt ───────────────────────────
print(f"\n[3/4] Sending test prompt to {MODEL_ID} ...")

TEST_PROMPT = "In one sentence, what is the IPL and when did it start?"

request_body = {
    "anthropic_version": "bedrock-2023-05-31",
    "max_tokens": 150,
    "messages": [
        {
            "role": "user",
            "content": TEST_PROMPT
        }
    ]
}

try:
    response = client.invoke_model(
        modelId=MODEL_ID,
        contentType="application/json",
        accept="application/json",
        body=json.dumps(request_body)
    )
    response_body = json.loads(response["body"].read())
    answer = response_body["content"][0]["text"]
    print(f"\n  Prompt : {TEST_PROMPT}")
    print(f"\n  Claude : {answer}")
    print(f"\n  ✅  Response received — model is live")
except Exception as e:
    print(f"  ❌  InvokeModel failed: {e}")
    print("\n  COMMON CAUSES:")
    print("    • Model not enabled → Bedrock Console → Model access → Request Claude 3 Haiku")
    print("    • Missing IAM permission → Add bedrock:InvokeModel to ipl-analytics-user")
    print("    • Wrong region → this script uses us-east-1; check your Bedrock region")
    exit(1)

# ── Step 4: Token usage summary ──────────────────────────────────
print("\n[4/4] Token usage:")
usage = response_body.get("usage", {})
input_tokens  = usage.get("input_tokens", 0)
output_tokens = usage.get("output_tokens", 0)
cost_estimate = (input_tokens * 0.25 + output_tokens * 1.25) / 1_000_000

print(f"  Input tokens  : {input_tokens}")
print(f"  Output tokens : {output_tokens}")
print(f"  Cost estimate : ${cost_estimate:.6f}  (~${cost_estimate * 1000:.4f} per 1,000 calls)")

print("\n" + "=" * 65)
print("  ✅  PHASE 2B COMPLETE — AWS Bedrock is connected and live")
print("  Next step: python scripts/build_chain.py")
print("=" * 65)
