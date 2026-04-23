"""Idempotent S3 bucket provisioning with Public Access Block + SSE-S3."""

from __future__ import annotations

import logging
from typing import Any

import boto3
from botocore.exceptions import ClientError

log = logging.getLogger("infra.s3")


def default_name(session: boto3.session.Session, region: str) -> str:
    """Account- and region-scoped name so reruns hit the same bucket."""
    account_id = session.client("sts").get_caller_identity()["Account"]
    return f"codegen-deepseek-{account_id}-{region}"


def ensure(
    session: boto3.session.Session, bucket: str, region: str
) -> None:
    s3 = session.client("s3")
    try:
        s3.head_bucket(Bucket=bucket)
        log.info("Bucket %s already exists", bucket)
        return
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        if code in ("403", "Forbidden"):
            raise RuntimeError(
                f"Bucket '{bucket}' exists but is owned by another account "
                "or inaccessible — pass a different --bucket."
            ) from exc
        if code not in ("404", "NoSuchBucket", "NotFound"):
            raise

    log.info("Creating S3 bucket %s in %s", bucket, region)
    kwargs: dict[str, Any] = {"Bucket": bucket}
    # us-east-1 is the odd one — must NOT include LocationConstraint.
    if region != "us-east-1":
        kwargs["CreateBucketConfiguration"] = {"LocationConstraint": region}
    s3.create_bucket(**kwargs)

    s3.put_public_access_block(
        Bucket=bucket,
        PublicAccessBlockConfiguration={
            "BlockPublicAcls": True,
            "IgnorePublicAcls": True,
            "BlockPublicPolicy": True,
            "RestrictPublicBuckets": True,
        },
    )
    s3.put_bucket_encryption(
        Bucket=bucket,
        ServerSideEncryptionConfiguration={
            "Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}],
        },
    )
    log.info("Bucket %s ready with PAB + AES256", bucket)


def empty_and_delete(session: boto3.session.Session, bucket: str) -> None:
    """Used only by teardown --delete-bucket."""
    s3 = session.client("s3")
    paginator = s3.get_paginator("list_object_versions")
    deleted = 0
    try:
        for page in paginator.paginate(Bucket=bucket):
            objs = [
                {"Key": v["Key"], "VersionId": v["VersionId"]}
                for v in (page.get("Versions") or []) + (page.get("DeleteMarkers") or [])
            ]
            if objs:
                s3.delete_objects(Bucket=bucket, Delete={"Objects": objs})
                deleted += len(objs)
    except ClientError as exc:
        if exc.response["Error"]["Code"] not in ("NoSuchBucket", "404"):
            raise
        log.info("Bucket %s already gone", bucket)
        return
    log.info("Deleted %d object version(s) from %s", deleted, bucket)
    s3.delete_bucket(Bucket=bucket)
    log.info("Deleted bucket %s", bucket)
