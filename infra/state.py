"""Tiny JSON state file that records what deploy.py provisioned.

Used by teardown.py to remove resources in reverse order. Intentionally
trivial — for a single-env dev tool, Terraform is overkill.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger("infra.state")

STATE_FILE = Path(".infra-state.json")


def load(path: Path = STATE_FILE) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        log.warning("State file %s is corrupt — treating as empty", path)
        return {}


def save(state: dict[str, Any], path: Path = STATE_FILE) -> None:
    path.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
    log.info("Wrote state to %s", path)


def delete(path: Path = STATE_FILE) -> None:
    if path.exists():
        path.unlink()
        log.info("Removed state file %s", path)
