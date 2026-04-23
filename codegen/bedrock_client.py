"""Bedrock Converse client with adaptive retry.

Relies on boto3's built-in adaptive retry mode for throttling / transient
errors — no third-party retry library required, so the Lambda stays lean.
"""

from __future__ import annotations

from typing import Any

import boto3
from botocore.config import Config

from .constants import SYSTEM_PROMPT
from .code_utils import build_user_message


def make_client(session: boto3.session.Session, region: str) -> Any:
    """Bedrock runtime client with retries and a 120s read timeout."""
    return session.client(
        "bedrock-runtime",
        region_name=region,
        config=Config(
            read_timeout=120,
            connect_timeout=10,
            retries={"max_attempts": 5, "mode": "adaptive"},
        ),
    )


def invoke(
    client: Any,
    *,
    model_id: str,
    prompt: str,
    language: str,
    max_tokens: int,
    temperature: float,
) -> dict[str, Any]:
    """Call DeepSeek via Converse and return a normalized result dict."""
    response = client.converse(
        modelId=model_id,
        messages=[
            {
                "role": "user",
                "content": [{"text": build_user_message(prompt, language)}],
            }
        ],
        system=[{"text": SYSTEM_PROMPT}],
        inferenceConfig={
            "maxTokens": max_tokens,
            "temperature": temperature,
            "topP": 0.9,
        },
    )
    output_message = response["output"]["message"]
    text = "".join(block.get("text", "") for block in output_message.get("content", []))
    return {
        "raw_output": text,
        "stop_reason": response.get("stopReason"),
        "usage": response.get("usage", {}),
        "model_id": model_id,
    }
