"""
===============================================================================
AWS Lambda - Code Generation using Amazon Bedrock (DeepSeek V3.2)
                 + automatic upload of generated code to S3
===============================================================================

WHAT THIS FUNCTION DOES
-----------------------
1. Receives a coding task (e.g. "reverse a linked list") as input.
2. Sends it to DeepSeek V3.2 on Amazon Bedrock.
3. Extracts the raw code from the model's markdown response.
4. Saves the code to an S3 bucket with a meaningful filename like:
       s3://<bucket>/<prefix>/generated_20260422_153045.py
   The extension is chosen based on the requested language so the file
   opens correctly in any IDE.

EXPECTED INPUT (JSON)
---------------------
{
    "prompt":      "Write a function that reverses a linked list", (required)
    "language":    "python",        (optional, default "python")
    "max_tokens":  2000,            (optional, default 2000, max 8000)
    "temperature": 0.2,             (optional, default 0.2)
    "model_id":    "deepseek.v3.2", (optional)
    "filename":    "my_solution"    (optional - override auto filename;
                                     extension is added automatically)
}

ENVIRONMENT VARIABLES
---------------------
    BEDROCK_REGION    - AWS region for Bedrock (default: us-east-1).
    DEFAULT_MODEL_ID  - Bedrock model id (default: "deepseek.v3.2").
    MAX_PROMPT_CHARS  - Max prompt length (default: 8000).
    S3_BUCKET         - REQUIRED. S3 bucket to upload generated code to.
    S3_PREFIX         - S3 folder / key prefix (default: "generated-code").
                        A trailing slash is added automatically if missing.

IAM PERMISSIONS REQUIRED
------------------------
    bedrock:InvokeModel  / bedrock:Converse on arn:aws:bedrock:*::foundation-model/deepseek.v3.2
    s3:PutObject         on arn:aws:s3:::<S3_BUCKET>/<S3_PREFIX>/*
===============================================================================
"""

# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------
import json       # Parse/serialize JSON payloads.
import logging    # CloudWatch logging.
import os         # Read environment variables.
import re         # Extract code from markdown fenced blocks.
from datetime import datetime, timezone  # Build timestamped filenames (UTC).
from typing import Any, Dict, Tuple

import boto3                                       # AWS SDK (Bedrock + S3).
from botocore.exceptions import BotoCoreError, ClientError

# ---------------------------------------------------------------------------
# Module-level configuration & AWS clients
#
# Defining clients here (not inside the handler) means Lambda creates them
# ONCE per warm container, saving 100-300ms on every subsequent invocation.
# ---------------------------------------------------------------------------
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Read config from environment variables with sensible defaults.
BEDROCK_REGION = os.environ.get("BEDROCK_REGION", "us-east-1")
DEFAULT_MODEL_ID = os.environ.get("DEFAULT_MODEL_ID", "deepseek.v3.2")
MAX_PROMPT_CHARS = int(os.environ.get("MAX_PROMPT_CHARS", "8000"))

# S3 destination. S3_BUCKET has no default - the handler will fail fast with
# a clear error if the env var is missing, rather than silently skipping the
# upload or trying to write to an unintended bucket.
S3_BUCKET = os.environ.get("S3_BUCKET")
# Normalize the prefix: strip leading slashes (S3 keys must not start with /)
# and ensure exactly one trailing slash so we can safely concatenate filenames.
S3_PREFIX = (os.environ.get("S3_PREFIX") or "generated-code").strip("/") + "/"

# Hard ceiling on DeepSeek V3.2 output tokens (Bedrock model card limit).
MAX_OUTPUT_TOKENS_CAP = 8000

# Bedrock data-plane client (model invocation) and S3 client (file upload).
bedrock_runtime = boto3.client("bedrock-runtime", region_name=BEDROCK_REGION)
s3_client = boto3.client("s3")  # S3 is global; no region needed for PutObject.

# System prompt - the "persona + rules" sent to the model on every call.
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
# Language -> file extension mapping.
#
# Used to give the uploaded S3 file a proper extension so it opens correctly
# in editors / IDEs. Add more entries here as you need them.
# ---------------------------------------------------------------------------
LANGUAGE_EXTENSIONS: Dict[str, str] = {
    "python":     "py",
    "javascript": "js",
    "typescript": "ts",
    "java":       "java",
    "kotlin":     "kt",
    "go":         "go",
    "golang":     "go",
    "rust":       "rs",
    "c":          "c",
    "cpp":        "cpp",
    "c++":        "cpp",
    "csharp":     "cs",
    "c#":         "cs",
    "ruby":       "rb",
    "php":        "php",
    "swift":      "swift",
    "scala":      "scala",
    "bash":       "sh",
    "shell":      "sh",
    "sh":         "sh",
    "sql":        "sql",
    "html":       "html",
    "css":        "css",
    "yaml":       "yaml",
    "yml":        "yml",
    "json":       "json",
    "r":          "r",
}

# Content-Type hints for a few common languages. Anything not in this map
# falls back to "text/plain" - still safe, just not as descriptive.
CONTENT_TYPES: Dict[str, str] = {
    "py":   "text/x-python",
    "js":   "application/javascript",
    "ts":   "application/typescript",
    "java": "text/x-java-source",
    "json": "application/json",
    "html": "text/html",
    "css":  "text/css",
    "sh":   "application/x-sh",
}


# ---------------------------------------------------------------------------
# Helper: build a uniform HTTP-style response
# ---------------------------------------------------------------------------
def _response(status: int, body: Dict[str, Any]) -> Dict[str, Any]:
    """Build an API-Gateway-compatible response envelope."""
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",  # Lock down to your origin in prod.
        },
        "body": json.dumps(body),  # API Gateway requires body to be a string.
    }


# ---------------------------------------------------------------------------
# Helper: normalize incoming event (API Gateway proxy vs direct invoke)
# ---------------------------------------------------------------------------
def _parse_event(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    Accept both API Gateway (event["body"] is a JSON string) and direct
    Lambda invocation (event is already a dict) shapes.
    """
    if isinstance(event.get("body"), str):
        try:
            return json.loads(event["body"])
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON body: {exc}") from exc
    return event


# ---------------------------------------------------------------------------
# Helper: build the user-facing prompt text
# ---------------------------------------------------------------------------
def _build_user_message(prompt: str, language: str) -> str:
    """Wrap the raw task in a small template that nudges the model output format."""
    return (
        f"Generate {language} code for the following task:\n\n"
        f"{prompt}\n\n"
        f"Respond with a single ```{language} ... ``` block."
    )


# ---------------------------------------------------------------------------
# Helper: strip the ```lang ... ``` markdown fences from the model output
# ---------------------------------------------------------------------------
# Matches an optional opening fence (```python, ```, etc.), captures the body,
# and an optional closing fence. DOTALL = '.' matches newlines too.
_FENCE_RE = re.compile(
    r"^\s*```[a-zA-Z0-9_+-]*\s*\n(?P<code>.*?)\n```\s*$",
    re.DOTALL,
)


def _extract_code(raw_output: str) -> str:
    """
    Remove surrounding markdown code fences so the file on S3 contains
    pure source code (no leading ```python / trailing ```).

    Falls back to the raw output if no fence is found - better to upload
    something imperfect than lose the generation entirely.
    """
    match = _FENCE_RE.match(raw_output.strip())
    if match:
        return match.group("code").rstrip() + "\n"  # Trailing newline = POSIX-friendly.
    return raw_output.strip() + "\n"


# ---------------------------------------------------------------------------
# Helper: pick the right file extension for the requested language
# ---------------------------------------------------------------------------
def _extension_for(language: str) -> str:
    """
    Return a file extension for the given language, e.g. "python" -> "py".
    Unknown languages fall back to "txt" so the upload still succeeds.
    """
    return LANGUAGE_EXTENSIONS.get(language.lower(), "txt")


# ---------------------------------------------------------------------------
# Helper: construct a meaningful, collision-resistant S3 key
# ---------------------------------------------------------------------------
def _build_s3_key(language: str, custom_name: str | None = None) -> Tuple[str, str]:
    """
    Build the S3 object key and return (key, filename).

    Convention:  <S3_PREFIX>/<base>_<UTC-YYYYMMDD_HHMMSS>.<ext>
    Example:     generated-code/python_20260422_153045.py

    The UTC timestamp makes filenames sortable and prevents collisions
    when the function is invoked repeatedly in parallel.
    """
    ext = _extension_for(language)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    # Allow the caller to override the base name (e.g. "linked_list_reverse");
    # otherwise default to the language name itself. We sanitize either way
    # to strip characters that would be awkward in an S3 key or filename.
    base = custom_name.strip() if custom_name else language.lower()
    base = re.sub(r"[^A-Za-z0-9_-]+", "_", base).strip("_") or "generated"

    filename = f"{base}_{timestamp}.{ext}"
    key = f"{S3_PREFIX}{filename}"
    return key, filename


# ---------------------------------------------------------------------------
# Helper: upload the generated code to S3
# ---------------------------------------------------------------------------
def _upload_to_s3(code: str, key: str, extension: str) -> str:
    """
    Write the code to S3 and return the s3:// URI.

    SSE-S3 (AES256) is enabled so objects are encrypted at rest with an
    AWS-managed key. If you need a customer-managed KMS key, switch to
    ServerSideEncryption="aws:kms" and add SSEKMSKeyId=<kms-arn>.
    """
    content_type = CONTENT_TYPES.get(extension, "text/plain")

    s3_client.put_object(
        Bucket=S3_BUCKET,
        Key=key,
        Body=code.encode("utf-8"),   # put_object expects bytes.
        ContentType=content_type,
        ServerSideEncryption="AES256",
    )
    return f"s3://{S3_BUCKET}/{key}"


# ---------------------------------------------------------------------------
# Core: call DeepSeek V3.2 on Bedrock via the Converse API
# ---------------------------------------------------------------------------
def invoke_deepseek(
    prompt: str,
    language: str,
    model_id: str,
    max_tokens: int,
    temperature: float,
) -> Dict[str, Any]:
    """
    Send the prompt to DeepSeek V3.2. We use Converse (not InvokeModel)
    because it is model-agnostic and handles DeepSeek's chat template
    internally, so we don't have to embed <|User|>/<|Assistant|> tokens.
    """
    messages = [
        {
            "role": "user",
            "content": [{"text": _build_user_message(prompt, language)}],
        }
    ]

    # Sampling config: low temperature for deterministic code generation.
    inference_config: Dict[str, Any] = {
        "maxTokens": max_tokens,
        "temperature": temperature,
        "topP": 0.9,
    }

    response = bedrock_runtime.converse(
        modelId=model_id,
        messages=messages,
        system=[{"text": SYSTEM_PROMPT}],  # Converse takes system separately.
        inferenceConfig=inference_config,
    )

    # Join all text blocks in the response (usually just one).
    output_message = response["output"]["message"]
    generated_text = "".join(
        block.get("text", "") for block in output_message.get("content", [])
    )

    return {
        "raw_output": generated_text,
        "stop_reason": response.get("stopReason"),
        "usage": response.get("usage", {}),
        "model_id": model_id,
    }


# ---------------------------------------------------------------------------
# Lambda entry point
# ---------------------------------------------------------------------------
def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    logger.info("Received event: %s", json.dumps(event)[:1000])

    # -------------------------------------------------------------------
    # STEP 0: Fail fast if S3 bucket isn't configured
    # -------------------------------------------------------------------
    # We do this before doing anything else - no point calling Bedrock
    # (which costs money) if we can't save the result afterwards.
    if not S3_BUCKET:
        return _response(
            500,
            {"error": "ConfigError", "message": "S3_BUCKET env var is not set."},
        )

    # -------------------------------------------------------------------
    # STEP 1: Parse and validate input
    # -------------------------------------------------------------------
    try:
        payload = _parse_event(event)
    except ValueError as exc:
        return _response(400, {"error": str(exc)})

    prompt = (payload.get("prompt") or "").strip()
    if not prompt:
        return _response(400, {"error": "'prompt' is required and cannot be empty."})
    if len(prompt) > MAX_PROMPT_CHARS:
        return _response(
            400,
            {"error": f"'prompt' exceeds max length of {MAX_PROMPT_CHARS} chars."},
        )

    language = (payload.get("language") or "python").strip().lower()
    model_id = payload.get("model_id") or DEFAULT_MODEL_ID
    max_tokens = int(payload.get("max_tokens", 2000))
    temperature = float(payload.get("temperature", 0.2))
    custom_filename = payload.get("filename")  # Optional base-name override.

    # Clamp numeric params to safe bounds (avoid a round-trip just to get
    # a ValidationException back from Bedrock).
    max_tokens = max(1, min(max_tokens, MAX_OUTPUT_TOKENS_CAP))
    temperature = max(0.0, min(temperature, 1.0))

    # -------------------------------------------------------------------
    # STEP 2: Call Bedrock
    # -------------------------------------------------------------------
    try:
        bedrock_result = invoke_deepseek(
            prompt, language, model_id, max_tokens, temperature
        )
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code", "ClientError")
        logger.exception("Bedrock ClientError: %s", error_code)
        # 429 for throttling (client should retry), 502 for everything else.
        status = 429 if error_code == "ThrottlingException" else 502
        return _response(status, {"error": error_code, "message": str(exc)})
    except BotoCoreError as exc:
        logger.exception("Bedrock BotoCoreError")
        return _response(502, {"error": "BedrockUnavailable", "message": str(exc)})
    except Exception as exc:  # noqa: BLE001 - last-resort guard
        logger.exception("Unexpected Bedrock error")
        return _response(500, {"error": "InternalError", "message": str(exc)})

    # -------------------------------------------------------------------
    # STEP 3: Clean up the model output and upload to S3
    # -------------------------------------------------------------------
    # Strip markdown fences so the S3 file is pure source code, not wrapped
    # in ```python ... ``` (which would break execution if you `python file.py`).
    clean_code = _extract_code(bedrock_result["raw_output"])
    s3_key, filename = _build_s3_key(language, custom_filename)
    extension = _extension_for(language)

    try:
        s3_uri = _upload_to_s3(clean_code, s3_key, extension)
    except ClientError as exc:
        # Most common causes: AccessDenied (IAM), NoSuchBucket (typo), or
        # bucket in a different region. All are actionable by the operator.
        error_code = exc.response.get("Error", {}).get("Code", "S3Error")
        logger.exception("S3 upload failed: %s", error_code)
        return _response(502, {"error": error_code, "message": str(exc)})
    except BotoCoreError as exc:
        logger.exception("S3 BotoCoreError")
        return _response(502, {"error": "S3Unavailable", "message": str(exc)})

    # -------------------------------------------------------------------
    # STEP 4: Return a summary to the caller
    # -------------------------------------------------------------------
    # We return the code inline too (handy for small snippets) PLUS the S3
    # location so the caller can download / share / version-control it.
    return _response(
        200,
        {
            "code": clean_code,
            "s3_uri": s3_uri,
            "s3_bucket": S3_BUCKET,
            "s3_key": s3_key,
            "filename": filename,
            "language": language,
            "extension": extension,
            "model_id": bedrock_result["model_id"],
            "stop_reason": bedrock_result["stop_reason"],
            "usage": bedrock_result["usage"],
        },
    )
