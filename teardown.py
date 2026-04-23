"""CLI: remove resources provisioned by deploy.py.

Reads `.infra-state.json` and deletes in reverse order.
The S3 bucket is kept by default (to preserve generated files) — pass
`--delete-bucket` to wipe it too.
"""

from __future__ import annotations

import argparse
import logging
import sys

from common.env import load_env, make_session
from common.logging_setup import configure as configure_logging
from infra import api_gateway, iam, lambda_fn, s3_bucket
from infra.state import delete as delete_state, load as load_state

log = logging.getLogger("teardown")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--delete-bucket", action="store_true",
                   help="Also empty and delete the S3 bucket (destructive).")
    p.add_argument("--yes", action="store_true",
                   help="Skip the interactive confirmation.")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def _confirm(state: dict, delete_bucket: bool) -> bool:
    print("About to delete:")
    print(f"  - API Gateway: {state.get('api_name')} ({state.get('api_id')})")
    print(f"  - Lambda:      {state.get('function_name')}")
    print(f"  - IAM role:    {state.get('role_name')}")
    if delete_bucket:
        print(f"  - S3 bucket:   {state.get('s3_bucket')}  (and ALL contents)")
    else:
        print(f"  - S3 bucket:   KEPT ({state.get('s3_bucket')})")
    answer = input("Proceed? [y/N] ").strip().lower()
    return answer == "y"


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    configure_logging(verbose=args.verbose)
    load_env()

    state = load_state()
    if not state:
        log.error("No .infra-state.json found — nothing to tear down.")
        return 1

    if not args.yes and not _confirm(state, args.delete_bucket):
        log.info("Aborted.")
        return 130

    region = state["region"]
    session = make_session(region)

    # Reverse order: API -> Lambda -> IAM -> (optional) bucket.
    if state.get("api_id"):
        api_gateway.delete_api(session, state["api_id"], region)

    if state.get("function_name"):
        lambda_fn.delete_function(session, state["function_name"], region)

    if state.get("role_name"):
        iam.delete_role(session, state["role_name"])

    if args.delete_bucket and state.get("s3_bucket"):
        s3_bucket.empty_and_delete(session, state["s3_bucket"])
    elif state.get("s3_bucket"):
        log.info("Bucket %s kept (pass --delete-bucket to remove).",
                 state["s3_bucket"])

    delete_state()
    log.info("Teardown complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
