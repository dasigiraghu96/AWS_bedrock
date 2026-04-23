"""Lambda entry point. Wraps codegen.pipeline.run with API-Gateway plumbing.

Invoked as: codegen.lambda_handler.lambda_handler
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from .constants import (
    DEFAULT_MAX_TOKENS,
    DEFAULT_MODEL_ID,
    DEFAULT_REGION,
    DEFAULT_S3_PREFIX,
    DEFAULT_TEMPERATURE,
)
from .pipeline import run

log = logging.getLogger()
log.setLevel(logging.INFO)

# Cold-start friendly: session reused across warm invocations.
_session = boto3.session.Session()

# Config from Lambda env vars (set by infra/lambda_fn.py during deploy).
S3_BUCKET = os.environ.get("S3_BUCKET")
S3_PREFIX = os.environ.get("S3_PREFIX", DEFAULT_S3_PREFIX)
BEDROCK_REGION = os.environ.get("BEDROCK_REGION", DEFAULT_REGION)
DEFAULT_MODEL = os.environ.get("DEFAULT_MODEL_ID", DEFAULT_MODEL_ID)


def _response(status: int, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(body),
    }


def _parse_event(event: dict[str, Any]) -> dict[str, Any]:
    """Accept API Gateway proxy events and direct Lambda invocations."""
    if isinstance(event.get("body"), str):
        try:
            return json.loads(event["body"])
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON body: {exc}") from exc
    return event


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    log.info("Received event: %s", json.dumps(event)[:1000])

    if not S3_BUCKET:
        return _response(500, {"error": "ConfigError",
                               "message": "S3_BUCKET env var is not set."})

    try:
        payload = _parse_event(event)
    except ValueError as exc:
        return _response(400, {"error": str(exc)})

    try:
        result = run(
            _session,
            prompt=(payload.get("prompt") or "").strip(),
            language=(payload.get("language") or "python"),
            bucket=S3_BUCKET,
            prefix=S3_PREFIX,
            model_id=payload.get("model_id") or DEFAULT_MODEL,
            max_tokens=int(payload.get("max_tokens", DEFAULT_MAX_TOKENS)),
            temperature=float(payload.get("temperature", DEFAULT_TEMPERATURE)),
            filename=payload.get("filename"),
            region=BEDROCK_REGION,
        )
    except ValueError as exc:
        return _response(400, {"error": str(exc)})
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "ClientError")
        log.exception("Bedrock/S3 ClientError: %s", code)
        status = 429 if code == "ThrottlingException" else 502
        return _response(status, {"error": code, "message": str(exc)})
    except BotoCoreError as exc:
        log.exception("BotoCoreError")
        return _response(502, {"error": "AwsUnavailable", "message": str(exc)})
    except Exception as exc:  # noqa: BLE001 - last-resort guard
        log.exception("Unexpected failure")
        return _response(500, {"error": "InternalError", "message": str(exc)})

    return _response(200, result)
