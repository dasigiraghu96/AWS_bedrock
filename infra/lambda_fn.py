"""Package the codegen/ folder and create/update the Lambda function."""

from __future__ import annotations

import io
import logging
import zipfile
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

log = logging.getLogger("infra.lambda")

LAMBDA_RUNTIME = "python3.13"
LAMBDA_HANDLER = "codegen.lambda_handler.lambda_handler"
LAMBDA_TIMEOUT = 120
LAMBDA_MEMORY_MB = 512


def package(source_dir: Path) -> bytes:
    """Zip the codegen/ package (only .py files) into a bytes blob.

    Resulting zip contains:
        codegen/__init__.py
        codegen/constants.py
        ...
    """
    if not source_dir.is_dir():
        raise FileNotFoundError(f"Lambda source dir not found: {source_dir}")

    buf = io.BytesIO()
    package_root = source_dir.parent  # arcnames are relative to this
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        files_added = 0
        for path in sorted(source_dir.rglob("*.py")):
            arcname = path.relative_to(package_root).as_posix()
            zf.write(path, arcname=arcname)
            files_added += 1
    log.info("Packaged %d file(s), %d bytes", files_added, buf.tell())
    return buf.getvalue()


def ensure_function(
    session: boto3.session.Session,
    *,
    name: str,
    zip_bytes: bytes,
    role_arn: str,
    env: dict[str, str],
    region: str,
) -> str:
    """Create or update the Lambda. Returns the function ARN."""
    client = session.client("lambda", region_name=region)

    try:
        existing = client.get_function(FunctionName=name)
        log.info("Updating existing Lambda %s", name)
        client.update_function_code(FunctionName=name, ZipFile=zip_bytes)
        # update_function_code is async — wait before patching configuration.
        waiter = client.get_waiter("function_updated_v2")
        waiter.wait(FunctionName=name)
        client.update_function_configuration(
            FunctionName=name,
            Role=role_arn,
            Handler=LAMBDA_HANDLER,
            Runtime=LAMBDA_RUNTIME,
            Timeout=LAMBDA_TIMEOUT,
            MemorySize=LAMBDA_MEMORY_MB,
            Environment={"Variables": env},
        )
        waiter.wait(FunctionName=name)
        return existing["Configuration"]["FunctionArn"]
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ResourceNotFoundException":
            raise

    log.info("Creating new Lambda %s", name)
    resp = client.create_function(
        FunctionName=name,
        Runtime=LAMBDA_RUNTIME,
        Role=role_arn,
        Handler=LAMBDA_HANDLER,
        Code={"ZipFile": zip_bytes},
        Timeout=LAMBDA_TIMEOUT,
        MemorySize=LAMBDA_MEMORY_MB,
        Environment={"Variables": env},
        Description="DeepSeek-V3.2 code generation → S3 upload.",
    )
    client.get_waiter("function_active_v2").wait(FunctionName=name)
    return resp["FunctionArn"]


def delete_function(session: boto3.session.Session, name: str, region: str) -> None:
    client = session.client("lambda", region_name=region)
    try:
        client.delete_function(FunctionName=name)
        log.info("Deleted Lambda %s", name)
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ResourceNotFoundException":
            log.info("Lambda %s already gone", name)
            return
        raise
