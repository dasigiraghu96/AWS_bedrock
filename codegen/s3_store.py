"""S3 upload helpers. Bucket *creation* lives in infra/s3_bucket.py."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import boto3

from .constants import CONTENT_TYPES


def upload(
    session: boto3.session.Session,
    *,
    bucket: str,
    key: str,
    code: str,
    extension: str,
) -> str:
    """PUT the code to S3 with SSE-S3 and a best-effort Content-Type. Returns s3:// URI."""
    s3 = session.client("s3")
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=code.encode("utf-8"),
        ContentType=CONTENT_TYPES.get(extension, "text/plain"),
        ServerSideEncryption="AES256",
    )
    return f"s3://{bucket}/{key}"


def save_local(out_dir: Path, filename: str, code: str) -> Path:
    """Mirror the S3 upload to the local filesystem for immediate editor use."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / filename
    path.write_text(code, encoding="utf-8")
    return path
