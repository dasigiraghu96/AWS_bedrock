"""CLI: generate code via DeepSeek V3.2 + upload to S3.

Two modes:
    --mode local   Run the whole pipeline locally (Bedrock + S3 direct).
    --mode remote  POST to the deployed API Gateway endpoint.

Example:
    python run.py --prompt "reverse a linked list"
    python run.py --mode remote --prompt "fizzbuzz" --language go
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from botocore.exceptions import BotoCoreError, ClientError
import requests

from codegen.constants import (
    DEFAULT_API_RESOURCE_PATH,
    DEFAULT_API_STAGE,
    DEFAULT_MAX_TOKENS,
    DEFAULT_MODEL_ID,
    DEFAULT_REGION,
    DEFAULT_S3_PREFIX,
    DEFAULT_TEMPERATURE,
)
from codegen import s3_store
from codegen.code_utils import build_s3_key, normalize_prefix
from codegen.pipeline import run as run_pipeline
from common import api_client
from common.env import load_env, make_session, region as env_region
from common.logging_setup import configure as configure_logging
from infra import s3_bucket
from infra.state import load as load_state

log = logging.getLogger("run")


# --------------------------------------------------------------------------- #
# Mode implementations
# --------------------------------------------------------------------------- #

def _resolve_bucket(args: argparse.Namespace, session) -> str:
    if args.bucket:
        return args.bucket
    state = load_state()
    if state.get("s3_bucket"):
        return state["s3_bucket"]
    return s3_bucket.default_name(session, args.region)


def _resolve_api_url(args: argparse.Namespace) -> str:
    if args.api_url:
        return args.api_url
    state = load_state()
    if state.get("invoke_url"):
        return state["invoke_url"]
    sys.exit(
        "No --api-url provided and no deployment found in .infra-state.json. "
        "Run `python deploy.py` first or pass --api-url."
    )


def _run_local(args: argparse.Namespace) -> dict:
    session = make_session(args.region)
    bucket = _resolve_bucket(args, session)
    s3_bucket.ensure(session, bucket, args.region)

    result = run_pipeline(
        session,
        prompt=args.prompt,
        language=args.language,
        bucket=bucket,
        prefix=args.prefix,
        model_id=args.model_id,
        max_tokens=args.max_tokens,
        temperature=args.temperature,
        filename=args.filename,
        region=args.region,
    )
    local_path = s3_store.save_local(
        Path(args.out_dir), result["filename"], result["code"]
    )
    log.info("Uploaded to %s", result["s3_uri"])
    log.info("Saved local copy to %s", local_path)
    result["mode"] = "local"
    result["local_path"] = str(local_path)
    return result


def _run_remote(args: argparse.Namespace) -> dict:
    url = _resolve_api_url(args)
    payload = {
        "prompt": args.prompt,
        "language": args.language,
        "max_tokens": args.max_tokens,
        "temperature": args.temperature,
    }
    if args.filename:
        payload["filename"] = args.filename
    if args.model_id != DEFAULT_MODEL_ID:
        payload["model_id"] = args.model_id

    body = api_client.call(url, payload)
    filename = body.get("filename") or build_s3_key(
        normalize_prefix(args.prefix), args.language, args.filename
    )[1]
    if body.get("code"):
        local_path = s3_store.save_local(Path(args.out_dir), filename, body["code"])
        body["local_path"] = str(local_path)
        log.info("Saved local copy to %s", local_path)
    body["mode"] = "remote"
    return body


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--prompt", required=True, help="Coding task description.")
    p.add_argument("--language", default="python", help="Target language.")
    p.add_argument("--mode", choices=["local", "remote"], default="local",
                   help="local = Bedrock+S3 directly; remote = API Gateway.")
    p.add_argument("--model-id", default=DEFAULT_MODEL_ID)
    p.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    p.add_argument("--temperature", type=float, default=DEFAULT_TEMPERATURE)
    p.add_argument("--filename", default=None, help="Override filename base (no extension).")
    p.add_argument("--bucket", default=None, help="Override S3 bucket.")
    p.add_argument("--prefix", default=DEFAULT_S3_PREFIX, help="S3 key prefix.")
    p.add_argument("--region", default=None, help="AWS region (defaults to .env / ap-south-1).")
    p.add_argument("--api-url", default=None,
                   help="API Gateway URL for --mode remote. Defaults to .infra-state.json.")
    p.add_argument("--api-stage", default=DEFAULT_API_STAGE)
    p.add_argument("--api-resource", default=DEFAULT_API_RESOURCE_PATH)
    p.add_argument("--out-dir", default="generated", help="Local folder for saved files.")
    p.add_argument("--json-only", action="store_true",
                   help="Emit only JSON on stdout; logs go to stderr.")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    configure_logging(verbose=args.verbose, json_only=args.json_only)
    load_env()
    args.region = args.region or env_region() or DEFAULT_REGION

    try:
        result = _run_remote(args) if args.mode == "remote" else _run_local(args)
    except ValueError as exc:
        log.error("Bad input: %s", exc)
        return 2
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "ClientError")
        log.error("AWS %s: %s", code, exc.response.get("Error", {}).get("Message"))
        if code == "AccessDeniedException":
            log.error("Hint: enable model access in the Bedrock console "
                      "(Model access → DeepSeek-V3.2 → Request access).")
        return 1
    except BotoCoreError as exc:
        log.error("AWS connectivity error: %s", exc)
        return 1
    except requests.HTTPError as exc:
        log.error("API Gateway returned %s: %s",
                  exc.response.status_code, exc.response.text[:500])
        return 1
    except requests.RequestException as exc:
        log.error("HTTP error: %s", exc)
        return 1

    if args.json_only:
        print(json.dumps(result, indent=2, default=str))
    else:
        print("\n===== GENERATED CODE =====")
        print(result.get("code", "").rstrip())
        print("\n===== SUMMARY =====")
        summary = {k: v for k, v in result.items() if k != "code"}
        print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
