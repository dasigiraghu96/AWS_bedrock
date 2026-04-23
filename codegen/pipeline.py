"""Orchestration: prompt -> Bedrock -> code extract -> S3 upload.

Shared by the local CLI (run.py --mode local) and the Lambda handler.
"""

from __future__ import annotations

from typing import Any

import boto3

from . import bedrock_client, code_utils, s3_store
from .constants import (
    DEFAULT_MAX_TOKENS,
    DEFAULT_MODEL_ID,
    DEFAULT_REGION,
    DEFAULT_TEMPERATURE,
    MAX_OUTPUT_TOKENS_CAP,
    MAX_PROMPT_CHARS,
)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(value, high))


def run(
    session: boto3.session.Session,
    *,
    prompt: str,
    language: str,
    bucket: str,
    prefix: str,
    model_id: str = DEFAULT_MODEL_ID,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    temperature: float = DEFAULT_TEMPERATURE,
    filename: str | None = None,
    region: str = DEFAULT_REGION,
) -> dict[str, Any]:
    """Run the full pipeline and return a structured summary dict.

    Raises:
        ValueError: if inputs are malformed (caller should return 4xx).
        botocore.exceptions.ClientError / BotoCoreError: if AWS fails.
    """
    if not prompt.strip():
        raise ValueError("prompt is required and cannot be empty")
    if len(prompt) > MAX_PROMPT_CHARS:
        raise ValueError(f"prompt exceeds max length of {MAX_PROMPT_CHARS} chars")

    max_tokens = int(_clamp(max_tokens, 1, MAX_OUTPUT_TOKENS_CAP))
    temperature = _clamp(temperature, 0.0, 1.0)
    language = language.strip().lower()
    prefix = code_utils.normalize_prefix(prefix)

    client = bedrock_client.make_client(session, region)
    bedrock_result = bedrock_client.invoke(
        client,
        model_id=model_id,
        prompt=prompt,
        language=language,
        max_tokens=max_tokens,
        temperature=temperature,
    )

    clean_code = code_utils.extract_code(bedrock_result["raw_output"])
    s3_key, out_filename = code_utils.build_s3_key(prefix, language, filename)
    extension = code_utils.extension_for(language)

    s3_uri = s3_store.upload(
        session, bucket=bucket, key=s3_key, code=clean_code, extension=extension
    )

    return {
        "code": clean_code,
        "s3_uri": s3_uri,
        "s3_bucket": bucket,
        "s3_key": s3_key,
        "filename": out_filename,
        "language": language,
        "extension": extension,
        "model_id": bedrock_result["model_id"],
        "stop_reason": bedrock_result["stop_reason"],
        "usage": bedrock_result["usage"],
    }
