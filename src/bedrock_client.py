"""
src/bedrock_client.py
---------------------
Thin wrapper around boto3 Bedrock Runtime.
Handles prompt formatting, token limits, and error handling.
Used by sql_generator.py and answer_formatter.py.
"""

import json
import boto3
import os

# ── Model config ────────────────────────────────────────────────
# Claude 3 Haiku: cheapest, fastest, sufficient quality for Text-to-SQL
# Swap to "anthropic.claude-3-sonnet-20240229-v1:0" for higher quality
MODEL_ID   = "us.anthropic.claude-3-5-haiku-20241022-v1:0"
AWS_REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")

# Max tokens for each call type
MAX_TOKENS_SQL    = 1024   # Complex CTEs (winner inference, multi-team aggregations) need ~600-900 tokens
MAX_TOKENS_ANSWER = 400    # Answer capped at ~150 words


def _get_client():
    """Return a Bedrock Runtime boto3 client.
    Reads credentials from environment variables or ~/.aws/credentials.
    In Streamlit Cloud, reads from st.secrets (set in app.py before import).
    """
    return boto3.client("bedrock-runtime", region_name=AWS_REGION)


def invoke(system_prompt: str, user_message: str, max_tokens: int = MAX_TOKENS_SQL) -> str:
    """
    Send a prompt to Claude via Bedrock and return the text response.

    Args:
        system_prompt : str — Instructions / context for Claude
        user_message  : str — The user's actual request
        max_tokens    : int — Hard cap on output length

    Returns:
        str — Claude's response text

    Raises:
        RuntimeError if Bedrock call fails
    """
    client = _get_client()

    request_body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": max_tokens,
        "system": system_prompt,
        "messages": [
            {"role": "user", "content": user_message}
        ],
        "temperature": 0.0,   # Deterministic — critical for SQL generation
        "top_p": 1.0,
    }

    try:
        response = client.invoke_model(
            modelId=MODEL_ID,
            contentType="application/json",
            accept="application/json",
            body=json.dumps(request_body),
        )
        body = json.loads(response["body"].read())
        return body["content"][0]["text"].strip()

    except client.exceptions.AccessDeniedException:
        raise RuntimeError(
            "Bedrock access denied. Check:\n"
            "  1. Model enabled in Bedrock Console (us-east-1 → Model access)\n"
            "  2. IAM policy includes bedrock:InvokeModel"
        )
    except Exception as e:
        raise RuntimeError(f"Bedrock InvokeModel failed: {e}")


def token_cost_estimate(input_tokens: int, output_tokens: int) -> float:
    """Estimate cost in USD for a Haiku call."""
    return (input_tokens * 0.25 + output_tokens * 1.25) / 1_000_000
