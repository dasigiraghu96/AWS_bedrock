"""Shared constants used by the local pipeline and the Lambda handler."""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Region / model defaults
# ---------------------------------------------------------------------------

DEFAULT_REGION = "ap-south-1"
DEFAULT_MODEL_ID = "deepseek.v3.2"

# ---------------------------------------------------------------------------
# Infra naming defaults (used by both deploy.py and teardown.py)
# ---------------------------------------------------------------------------

DEFAULT_FUNCTION_NAME = "codegen-deepseek"
DEFAULT_ROLE_NAME = "codegen-deepseek-lambda-role"
DEFAULT_API_NAME = "codegen-deepseek-api"
DEFAULT_API_STAGE = "dev"
DEFAULT_API_RESOURCE_PATH = "code-generation"
DEFAULT_S3_PREFIX = "generated-code"

# ---------------------------------------------------------------------------
# Input limits
# ---------------------------------------------------------------------------

MAX_PROMPT_CHARS = 8000
MAX_OUTPUT_TOKENS_CAP = 8000
DEFAULT_MAX_TOKENS = 2000
DEFAULT_TEMPERATURE = 0.2

# ---------------------------------------------------------------------------
# System prompt sent to DeepSeek on every call
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = (
    "You are an expert software engineer that writes clean, production-grade code. "
    "Follow these rules strictly:\n"
    "1. Return ONLY the code inside a single fenced markdown block tagged with the language.\n"
    "2. Include concise inline comments explaining non-obvious logic.\n"
    "3. Handle edge cases and validate inputs where appropriate.\n"
    "4. Do not include usage examples, explanations, or prose outside the code block "
    "unless the user explicitly asks for them."
)

# ---------------------------------------------------------------------------
# Language -> file extension
# ---------------------------------------------------------------------------

LANGUAGE_EXTENSIONS: dict[str, str] = {
    "python": "py", "javascript": "js", "typescript": "ts", "java": "java",
    "kotlin": "kt", "go": "go", "golang": "go", "rust": "rs", "c": "c",
    "cpp": "cpp", "c++": "cpp", "csharp": "cs", "c#": "cs", "ruby": "rb",
    "php": "php", "swift": "swift", "scala": "scala", "bash": "sh",
    "shell": "sh", "sh": "sh", "sql": "sql", "html": "html", "css": "css",
    "yaml": "yaml", "yml": "yml", "json": "json", "r": "r",
}

# ---------------------------------------------------------------------------
# Content-Type hints for common source files on S3
# ---------------------------------------------------------------------------

CONTENT_TYPES: dict[str, str] = {
    "py": "text/x-python",
    "js": "application/javascript",
    "ts": "application/typescript",
    "java": "text/x-java-source",
    "json": "application/json",
    "html": "text/html",
    "css": "text/css",
    "sh": "application/x-sh",
}
