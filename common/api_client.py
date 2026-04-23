"""Thin HTTP client for calling the deployed API Gateway endpoint."""

from __future__ import annotations

import json
import logging
from typing import Any

import requests

log = logging.getLogger("common.api")


def call(url: str, payload: dict[str, Any], timeout: int = 180) -> dict[str, Any]:
    """POST JSON to `url` and return the parsed response body.

    Unwraps a double-wrapped body if API Gateway returned the Lambda proxy
    response shape (`{"statusCode": ..., "body": "..."}`) to a non-proxy
    integration — safe no-op when it's already a dict.
    """
    log.info("POST %s", url)
    resp = requests.post(
        url,
        json=payload,
        headers={"Content-Type": "application/json"},
        timeout=timeout,
    )
    log.info("HTTP %s (%.1f KB)", resp.status_code, len(resp.content) / 1024)
    resp.raise_for_status()

    body = resp.json()
    if isinstance(body, dict) and isinstance(body.get("body"), str):
        try:
            body = json.loads(body["body"])
        except json.JSONDecodeError:
            pass
    return body
