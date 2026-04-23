"""CLI: provision (or update) the S3 bucket, IAM role, Lambda, and REST API.

Idempotent — safe to re-run. Writes `.infra-state.json` for teardown.py.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from codegen.constants import (
    DEFAULT_API_NAME,
    DEFAULT_API_RESOURCE_PATH,
    DEFAULT_API_STAGE,
    DEFAULT_FUNCTION_NAME,
    DEFAULT_MODEL_ID,
    DEFAULT_REGION,
    DEFAULT_ROLE_NAME,
    DEFAULT_S3_PREFIX,
)
from codegen.code_utils import normalize_prefix
from common.env import load_env, make_session, region as env_region
from common.logging_setup import configure as configure_logging
from infra import api_gateway, iam, lambda_fn, s3_bucket
from infra.state import save as save_state

log = logging.getLogger("deploy")

CODEGEN_SRC = Path(__file__).parent / "codegen"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--bucket", default=None,
                   help="S3 bucket (default: codegen-deepseek-<account>-<region>).")
    p.add_argument("--prefix", default=DEFAULT_S3_PREFIX)
    p.add_argument("--function-name", default=DEFAULT_FUNCTION_NAME)
    p.add_argument("--role-name", default=DEFAULT_ROLE_NAME)
    p.add_argument("--api-name", default=DEFAULT_API_NAME)
    p.add_argument("--api-stage", default=DEFAULT_API_STAGE)
    p.add_argument("--api-resource", default=DEFAULT_API_RESOURCE_PATH)
    p.add_argument("--model-id", default=DEFAULT_MODEL_ID)
    p.add_argument("--region", default=None, help="AWS region (default: .env).")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    configure_logging(verbose=args.verbose)
    load_env()

    region = args.region or env_region() or DEFAULT_REGION
    session = make_session(region)
    prefix = normalize_prefix(args.prefix)

    log.info("Region: %s", region)

    # 1. S3 bucket
    bucket = args.bucket or s3_bucket.default_name(session, region)
    s3_bucket.ensure(session, bucket, region)

    # 2. IAM role (with Bedrock + S3 permissions scoped to this bucket/prefix)
    role_arn = iam.ensure_role(
        session,
        role_name=args.role_name,
        bucket=bucket,
        prefix=prefix,
        region=region,
        model_id=args.model_id,
    )

    # 3. Package + deploy the Lambda
    zip_bytes = lambda_fn.package(CODEGEN_SRC)
    lambda_env = {
        "S3_BUCKET": bucket,
        "S3_PREFIX": prefix,
        "BEDROCK_REGION": region,
        "DEFAULT_MODEL_ID": args.model_id,
    }
    lambda_arn = lambda_fn.ensure_function(
        session,
        name=args.function_name,
        zip_bytes=zip_bytes,
        role_arn=role_arn,
        env=lambda_env,
        region=region,
    )

    # 4. REST API wired to the Lambda
    api = api_gateway.ensure_api(
        session,
        api_name=args.api_name,
        resource_path=args.api_resource,
        stage=args.api_stage,
        lambda_arn=lambda_arn,
        lambda_name=args.function_name,
        region=region,
    )

    # 5. Record everything teardown.py needs
    state = {
        "region": region,
        "s3_bucket": bucket,
        "s3_prefix": prefix,
        "bucket_created_by_us": True,
        "role_name": args.role_name,
        "role_arn": role_arn,
        "function_name": args.function_name,
        "function_arn": lambda_arn,
        "api_id": api["api_id"],
        "api_name": args.api_name,
        "api_stage": api["stage"],
        "api_resource": args.api_resource,
        "invoke_url": api["invoke_url"],
        "model_id": args.model_id,
    }
    save_state(state)

    log.info("Deployment complete.")
    print(json.dumps(state, indent=2))
    print("\nTest with:")
    print(f'  python run.py --mode remote --prompt "reverse a linked list"')
    print(f'  curl -X POST {api["invoke_url"]} -H "Content-Type: application/json" '
          f'-d \'{{"prompt":"fizzbuzz","language":"go"}}\'')
    return 0


if __name__ == "__main__":
    sys.exit(main())
