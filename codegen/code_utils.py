"""Pure-Python helpers: markdown fence extraction, key building, prompt shaping.

Kept dependency-free so the Lambda zip stays small.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from .constants import LANGUAGE_EXTENSIONS

# Match an optional opening fence (```python, ```, etc.) + body + optional close.
_FENCE_RE = re.compile(
    r"^\s*```[a-zA-Z0-9_+-]*\s*\n(?P<code>.*?)\n```\s*$",
    re.DOTALL,
)

# Reject characters that are awkward inside filenames and S3 keys.
_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9_-]+")


def extract_code(raw_output: str) -> str:
    """Strip surrounding ```lang ... ``` fences; return POSIX-friendly text."""
    match = _FENCE_RE.match(raw_output.strip())
    if match:
        return match.group("code").rstrip() + "\n"
    return raw_output.strip() + "\n"


def extension_for(language: str) -> str:
    return LANGUAGE_EXTENSIONS.get(language.lower(), "txt")


def build_s3_key(
    prefix: str, language: str, custom_name: str | None = None
) -> tuple[str, str]:
    """Return (s3_key, filename). Key format: <prefix><base>_<utc-ts>.<ext>."""
    ext = extension_for(language)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    base = (custom_name or language).strip()
    base = _UNSAFE_CHARS.sub("_", base).strip("_") or "generated"
    filename = f"{base}_{ts}.{ext}"
    return f"{prefix}{filename}", filename


def build_user_message(prompt: str, language: str) -> str:
    """Wrap the raw task in a template that nudges the model output format."""
    return (
        f"Generate {language} code for the following task:\n\n"
        f"{prompt}\n\n"
        f"Respond with a single ```{language} ... ``` block."
    )


def normalize_prefix(prefix: str) -> str:
    """Strip leading slashes and ensure exactly one trailing slash."""
    return prefix.strip("/") + "/"
