"""IAM role + inline policy for the Lambda execution role. Idempotent."""

from __future__ import annotations

import json
import logging
import time

import boto3
from botocore.exceptions import ClientError

log = logging.getLogger("infra.iam")

INLINE_POLICY_NAME = "codegen-deepseek-inline"

TRUST_POLICY = {
    "Version": "2012-10-17",
    "Statement": [{
        "Effect": "Allow",
        "Principal": {"Service": "lambda.amazonaws.com"},
        "Action": "sts:AssumeRole",
    }],
}

BASIC_EXECUTION_ARN = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"


def _inline_policy(bucket: str, prefix: str, region: str, model_id: str) -> dict:
    model_arn = f"arn:aws:bedrock:{region}::foundation-model/{model_id}"
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "BedrockInvoke",
                "Effect": "Allow",
                "Action": ["bedrock:InvokeModel", "bedrock:Converse"],
                "Resource": [model_arn],
            },
            {
                "Sid": "S3WriteGeneratedCode",
                "Effect": "Allow",
                "Action": ["s3:PutObject", "s3:PutObjectAcl"],
                "Resource": [f"arn:aws:s3:::{bucket}/{prefix.rstrip('/')}/*"],
            },
        ],
    }


def ensure_role(
    session: boto3.session.Session,
    *,
    role_name: str,
    bucket: str,
    prefix: str,
    region: str,
    model_id: str,
) -> str:
    iam = session.client("iam")

    try:
        role = iam.get_role(RoleName=role_name)["Role"]
        log.info("IAM role %s already exists", role_name)
        created = False
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "NoSuchEntity":
            raise
        log.info("Creating IAM role %s", role_name)
        role = iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(TRUST_POLICY),
            Description="Execution role for the DeepSeek codegen Lambda.",
        )["Role"]
        created = True

    iam.attach_role_policy(RoleName=role_name, PolicyArn=BASIC_EXECUTION_ARN)
    iam.put_role_policy(
        RoleName=role_name,
        PolicyName=INLINE_POLICY_NAME,
        PolicyDocument=json.dumps(
            _inline_policy(bucket=bucket, prefix=prefix, region=region, model_id=model_id)
        ),
    )
    log.info("Attached inline policy %s to %s", INLINE_POLICY_NAME, role_name)

    if created:
        # IAM is eventually consistent — Lambda will reject the role for a few
        # seconds after creation. Pause once on first create to avoid the flaky
        # InvalidParameterValueException "role cannot be assumed" failure.
        log.info("Waiting 10s for new IAM role to propagate...")
        time.sleep(10)

    return role["Arn"]


def delete_role(session: boto3.session.Session, role_name: str) -> None:
    iam = session.client("iam")
    try:
        for p in iam.list_attached_role_policies(RoleName=role_name).get("AttachedPolicies", []):
            iam.detach_role_policy(RoleName=role_name, PolicyArn=p["PolicyArn"])
        for name in iam.list_role_policies(RoleName=role_name).get("PolicyNames", []):
            iam.delete_role_policy(RoleName=role_name, PolicyName=name)
        iam.delete_role(RoleName=role_name)
        log.info("Deleted IAM role %s", role_name)
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "NoSuchEntity":
            log.info("IAM role %s already gone", role_name)
            return
        raise
