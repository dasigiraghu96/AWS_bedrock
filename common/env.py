"""Load .env and build a boto3 Session keyed to the configured region."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import boto3
from dotenv import load_dotenv

from codegen.constants import DEFAULT_REGION

_REQUIRED = ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY")


def load_env(env_path: Path | None = None) -> None:
    """Load .env from repo root (or the given path). Exits on missing creds."""
    path = env_path or (Path(__file__).resolve().parent.parent / ".env")
    load_dotenv(path)
    missing = [k for k in _REQUIRED if not os.getenv(k)]
    if missing:
        sys.exit(f"Missing environment variables: {', '.join(missing)} (checked {path})")


def region() -> str:
    return os.getenv("AWS_DEFAULT_REGION") or DEFAULT_REGION


def make_session(region_name: str | None = None) -> boto3.session.Session:
    """Return a boto3 session that picks up creds from env / .env."""
    return boto3.session.Session(region_name=region_name or region())
