"""Smoke tests for pure-Python helpers — no AWS calls."""

from __future__ import annotations

from codegen.code_utils import (
    build_s3_key,
    build_user_message,
    extension_for,
    extract_code,
    normalize_prefix,
)


def test_extract_code_strips_fences():
    raw = "```python\nprint('hi')\n```"
    assert extract_code(raw) == "print('hi')\n"


def test_extract_code_handles_missing_fence():
    raw = "print('hi')"
    assert extract_code(raw) == "print('hi')\n"


def test_extract_code_preserves_inner_content():
    raw = "```go\npackage main\n\nfunc main() {}\n```"
    assert "package main" in extract_code(raw)


def test_extension_for_known_language():
    assert extension_for("python") == "py"
    assert extension_for("Go") == "go"
    assert extension_for("c++") == "cpp"


def test_extension_for_unknown_language():
    assert extension_for("brainfuck") == "txt"


def test_build_s3_key_shape():
    key, filename = build_s3_key("generated-code/", "python")
    assert key.startswith("generated-code/python_")
    assert key.endswith(".py")
    assert filename.endswith(".py")


def test_build_s3_key_sanitizes_custom_name():
    _, filename = build_s3_key("generated-code/", "python", "my name / bad$chars")
    assert " " not in filename
    assert "/" not in filename
    assert "$" not in filename


def test_normalize_prefix_adds_trailing_slash():
    assert normalize_prefix("generated-code") == "generated-code/"
    assert normalize_prefix("/generated-code/") == "generated-code/"


def test_build_user_message_mentions_language():
    msg = build_user_message("reverse a list", "rust")
    assert "rust" in msg.lower()
    assert "reverse a list" in msg
