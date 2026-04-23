# DeepSeek V3.2 Code Generation on AWS

A small, production-ready project that turns a natural-language coding task
into a source file on S3 — using **DeepSeek V3.2** on **Amazon Bedrock**.

Three entry points, one codebase:

| Script | What it does |
|---|---|
| `run.py` | Generate code. Runs either **locally** (laptop → Bedrock → S3) or **remotely** (laptop → API Gateway → Lambda → Bedrock → S3). |
| `deploy.py` | Provision the full AWS stack (S3 bucket, IAM role, Lambda, REST API). Idempotent. |
| `teardown.py` | Remove the AWS stack. Keeps the S3 bucket unless you pass `--delete-bucket`. |

The Lambda handler and the local CLI **share the same pipeline code**
(`codegen/pipeline.py`), so behaviour is byte-identical in both modes.

---

## Architecture

```
                                ┌──────────────────────────┐
                                │  Amazon Bedrock          │
                                │  model: deepseek.v3.2    │
                                └───────────▲──────────────┘
                                            │ Converse API
           local mode ─────────────────────┐│
                                           ││
   ┌─────────┐   HTTPS POST   ┌──────────┐ ││  ┌──────────┐
   │ run.py  │ ─────────────▶ │   API    │─┼┼─▶│  Lambda  │
   │  CLI    │                │ Gateway  │ │└─▶│ codegen  │
   └────┬────┘                └──────────┘ │   └────┬─────┘
        │                                  │        │
        │ local mode (direct)              │        │ PutObject
        ▼                                  │        ▼
   ┌──────────┐                            │   ┌──────────┐
   │ Bedrock  │                            │   │    S3    │
   └──────────┘                            │   │  bucket  │
        │                                  │   └──────────┘
        ▼                                  │
   ┌──────────┐                            │
   │    S3    │◀───────────────────────────┘
   └──────────┘
```

---

## Prerequisites

- **Python 3.11+** (tested on 3.13).
- **AWS account** with:
  - An IAM user / role whose credentials can create S3 buckets, IAM roles,
    Lambda functions, and REST APIs.
  - **Bedrock model access** for DeepSeek V3.2 enabled in your chosen region
    (once, via AWS Console → Bedrock → *Model access* → *Request access*).
- A `.env` file at the repo root:

  ```
  AWS_ACCESS_KEY_ID=AKIA...
  AWS_SECRET_ACCESS_KEY=...
  AWS_DEFAULT_REGION=ap-south-1
  ```

---

## Install

```bash
pip install -r requirements.txt
```

Dependencies:

| Package | Used where |
|---|---|
| `boto3` | everywhere (Bedrock, S3, Lambda, IAM, API Gateway) |
| `python-dotenv` | `common/env.py` for local `.env` loading |
| `requests` | `common/api_client.py` for remote-mode HTTP calls |

The Lambda zip depends only on boto3 (pre-installed in the runtime) — no
third-party packages are bundled.

---

## Directory layout

```
AWS/
├── .env                     AWS credentials (gitignored)
├── .gitignore
├── requirements.txt
│
├── run.py                   ▶ CLI: generate code (local or remote)
├── deploy.py                ▶ CLI: provision AWS resources
├── teardown.py              ▶ CLI: remove AWS resources
│
├── codegen/                 Lambda-packageable core (stdlib + boto3 only)
│   ├── constants.py         SYSTEM_PROMPT, extension maps, defaults
│   ├── code_utils.py        markdown fence stripping, S3 key building
│   ├── bedrock_client.py    Converse client with adaptive retry
│   ├── s3_store.py          put_object + local save
│   ├── pipeline.py          orchestration (shared by CLI + Lambda)
│   └── lambda_handler.py    λ entry: codegen.lambda_handler.lambda_handler
│
├── infra/                   Provisioning (not shipped to Lambda)
│   ├── s3_bucket.py         ensure / empty / delete
│   ├── iam.py               role + scoped inline policy (idempotent)
│   ├── lambda_fn.py         zip + create/update
│   ├── api_gateway.py       REST API + POST /code-generation + AWS_PROXY
│   └── state.py             .infra-state.json read/write
│
├── common/                  CLI helpers
│   ├── env.py               .env loader + boto3 session factory
│   ├── logging_setup.py     unified logger config
│   └── api_client.py        requests POST to API Gateway
│
├── generated/               mirrored outputs (gitignored)
└── tests/
    └── test_code_utils.py   9 unit tests (no AWS calls)
```

---

## Configuration

Everything is driven by `.env` + CLI flags. Defaults live in
[codegen/constants.py](codegen/constants.py):

| Constant | Default | Overridden by |
|---|---|---|
| `DEFAULT_REGION` | `ap-south-1` | `AWS_DEFAULT_REGION` env or `--region` |
| `DEFAULT_MODEL_ID` | `deepseek.v3.2` | `--model-id` |
| `DEFAULT_FUNCTION_NAME` | `codegen-deepseek` | `--function-name` |
| `DEFAULT_ROLE_NAME` | `codegen-deepseek-lambda-role` | `--role-name` |
| `DEFAULT_API_NAME` | `codegen-deepseek-api` | `--api-name` |
| `DEFAULT_API_STAGE` | `dev` | `--api-stage` |
| `DEFAULT_API_RESOURCE_PATH` | `code-generation` | `--api-resource` |
| `DEFAULT_S3_PREFIX` | `generated-code` | `--prefix` |
| `MAX_PROMPT_CHARS` | `8000` | — |
| `MAX_OUTPUT_TOKENS_CAP` | `8000` | clamped |

---

## Usage

### 1. Local mode (no deployment required)

Runs the same pipeline directly from your laptop — no Lambda, no API Gateway.
Will auto-create a per-account S3 bucket on first run
(`codegen-deepseek-<account-id>-<region>`) with Public Access Block and
AES256 default encryption.

```bash
python run.py --prompt "reverse a linked list"
python run.py --prompt "binary search" --language java
python run.py --prompt "fizzbuzz" --language go --max-tokens 800
python run.py --prompt "parse CSV" --json-only > result.json
```

Output is printed to stdout **and** uploaded to S3 **and** saved under
`generated/`.

### 2. Deploy to AWS

```bash
python deploy.py
```

This is **idempotent** — re-run it any time to push a new Lambda zip or to
verify the stack is intact. On first run it:

1. Ensures the S3 bucket (creates if missing).
2. Creates / updates the IAM role `codegen-deepseek-lambda-role` with
   an inline policy scoped to **that bucket/prefix** and the DeepSeek model
   ARN only.
3. Zips the `codegen/` package (~6 KB — no vendored deps) and creates the
   Lambda `codegen-deepseek` (python3.13, 512 MB, 120 s timeout).
4. Creates REST API `codegen-deepseek-api` with `POST /code-generation`
   wired as `AWS_PROXY` to the Lambda, deployed to stage `dev`.
5. Writes `.infra-state.json` so `teardown.py` and `run.py --mode remote`
   can auto-discover everything.

Example output:

```json
{
  "region": "ap-south-1",
  "s3_bucket": "codegen-deepseek-123456789012-ap-south-1",
  "function_arn": "arn:aws:lambda:ap-south-1:123456789012:function:codegen-deepseek",
  "api_id": "1l1dlwil6j",
  "invoke_url": "https://1l1dlwil6j.execute-api.ap-south-1.amazonaws.com/dev/code-generation"
}
```

### 3. Remote mode (hits the deployed API Gateway)

```bash
python run.py --mode remote --prompt "merge sort"
python run.py --mode remote --prompt "fizzbuzz" --language go
```

If `--api-url` is not supplied, the URL is read from `.infra-state.json`.
You can also point at any other already-deployed endpoint:

```bash
python run.py --mode remote \
  --api-url https://qmuto3ma63.execute-api.ap-south-1.amazonaws.com/dev/code-generation \
  --prompt "reverse a string"
```

Or bypass the script entirely:

```bash
curl -X POST https://<api-id>.execute-api.<region>.amazonaws.com/dev/code-generation \
  -H "Content-Type: application/json" \
  -d '{"prompt":"fizzbuzz","language":"go"}'
```

### 4. Tear down

```bash
python teardown.py                     # keeps the bucket
python teardown.py --delete-bucket     # also empties + deletes the bucket
python teardown.py --yes               # skip the interactive confirmation
```

Reads `.infra-state.json` and deletes in reverse order: API Gateway → Lambda
→ IAM role → (optional) S3 bucket.

---

## Request / response format

Both `run.py --mode remote` and direct HTTP calls to the API use the same
JSON payload:

```json
{
  "prompt":      "Write a function that reverses a linked list",
  "language":    "python",
  "max_tokens":  2000,
  "temperature": 0.2,
  "filename":    "linked_list_reverse",
  "model_id":    "deepseek.v3.2"
}
```

Only `prompt` is required.

Successful response (both modes return the same shape):

```json
{
  "code": "def reverse_linked_list(head):\n    ...",
  "s3_uri": "s3://codegen-deepseek-.../generated-code/python_20260424_101530.py",
  "s3_bucket": "codegen-deepseek-...",
  "s3_key": "generated-code/python_20260424_101530.py",
  "filename": "python_20260424_101530.py",
  "language": "python",
  "extension": "py",
  "model_id": "deepseek.v3.2",
  "stop_reason": "end_turn",
  "usage": {"inputTokens": 116, "outputTokens": 169, "totalTokens": 285}
}
```

### Error codes

| Status | Meaning |
|---|---|
| 400 | Bad input (missing / empty prompt, prompt too long, invalid JSON) |
| 429 | `ThrottlingException` from Bedrock — safe to retry |
| 500 | Config error (e.g. `S3_BUCKET` env var missing on Lambda) |
| 502 | Bedrock or S3 unavailable |

---

## Module walk-through

### `codegen/constants.py`
All tunables in one place — system prompt, default region/model, infra names,
prompt-length caps, language→extension map, and Content-Type hints.

### `codegen/code_utils.py`
Pure-Python helpers — zero dependencies:
- `extract_code()` strips the ```` ```lang ... ``` ```` markdown fences.
- `build_s3_key()` makes timestamped, collision-resistant S3 keys.
- `build_user_message()` wraps the task in a template that steers the model
  toward a fenced response.
- `normalize_prefix()` guarantees the S3 prefix ends in exactly one `/`.

### `codegen/bedrock_client.py`
Creates the Bedrock runtime client with a 120 s read timeout and
**adaptive retry** (`max_attempts=5`), and exposes `invoke(...)`, which
calls the Converse API and returns a normalized dict.

### `codegen/s3_store.py`
`upload()` — `put_object` with SSE-S3 (`AES256`). `save_local()` mirrors
the same file to `generated/` for immediate editor use.

### `codegen/pipeline.py`
Validates input → builds the user message → calls Bedrock → extracts
clean code → uploads to S3. Used by both the CLI and the Lambda handler,
so remote and local outputs stay byte-identical.

### `codegen/lambda_handler.py`
Lambda entry point (`codegen.lambda_handler.lambda_handler`). Parses both
API-Gateway-proxy events (body as JSON string) and direct invokes, calls
`pipeline.run(...)`, and converts boto3 errors into HTTP status codes.

### `infra/s3_bucket.py`
`ensure(...)` creates the bucket with Public Access Block + AES256 if it
doesn't exist, skips creation if it does, raises on cross-account conflicts.
`empty_and_delete(...)` is used only by `teardown.py --delete-bucket` and
handles versioned objects safely.

### `infra/iam.py`
`ensure_role(...)` creates the Lambda execution role, attaches
`AWSLambdaBasicExecutionRole`, and replaces the inline policy on every run
so permissions track your current config. Policy is **least-privilege**:

- `bedrock:InvokeModel`, `bedrock:Converse` only on the specific DeepSeek
  model ARN.
- `s3:PutObject`, `s3:PutObjectAcl` only on `<bucket>/<prefix>/*`.

On first creation there's a 10 s sleep to wait out IAM's eventual
consistency (otherwise the first Lambda create can fail with
`InvalidParameterValueException: role cannot be assumed`).

### `infra/lambda_fn.py`
`package(...)` zips every `.py` under `codegen/` into an in-memory buffer —
no `boto3`, no dependencies — keeping the deployment artifact ~6 KB.
`ensure_function(...)` does a safe *create-or-update*: `update_function_code`
→ wait → `update_function_configuration` → wait; `create_function`
otherwise. The waiters are important — Lambda rejects config updates while
the previous one is still "in progress".

### `infra/api_gateway.py`
Builds a REST API with a single `POST /code-generation` resource using
`AWS_PROXY` integration to the Lambda. Adds the `lambda:InvokeFunction`
permission for the API with a stable `StatementId` so re-deploys are
idempotent. Deploys to stage `dev`, returns the invoke URL.

### `infra/state.py`
Tiny JSON read/write around `.infra-state.json`. Stores everything
`teardown.py` and `run.py --mode remote` need to find resources without
re-parsing the AWS account state.

### `common/env.py`, `common/logging_setup.py`, `common/api_client.py`
Thin helpers used only by the local CLIs: `.env` loading, logging config
(sends logs to stderr when `--json-only` is set so stdout stays clean JSON),
and a `requests.post` wrapper that transparently unwraps double-wrapped
API Gateway proxy responses.

---

## Testing

### Unit tests

```bash
python -m pytest tests/ -v
```

```
tests/test_code_utils.py::test_extract_code_strips_fences PASSED
tests/test_code_utils.py::test_extract_code_handles_missing_fence PASSED
tests/test_code_utils.py::test_extract_code_preserves_inner_content PASSED
tests/test_code_utils.py::test_extension_for_known_language PASSED
tests/test_code_utils.py::test_extension_for_unknown_language PASSED
tests/test_code_utils.py::test_build_s3_key_shape PASSED
tests/test_code_utils.py::test_build_s3_key_sanitizes_custom_name PASSED
tests/test_code_utils.py::test_normalize_prefix_adds_trailing_slash PASSED
tests/test_code_utils.py::test_build_user_message_mentions_language PASSED

============================== 9 passed in 0.09s ==============================
```

All tests are hermetic — they exercise only the pure-Python helpers in
`codegen/code_utils.py` and never touch AWS.

### End-to-end smoke tests

Local pipeline (creates the bucket on first run, ~5 s after):

```bash
python run.py --prompt "reverse a linked list"
```

Remote pipeline (requires `python deploy.py` to have been run):

```bash
python run.py --mode remote --prompt "fizzbuzz" --language go
```

Verify objects landed in S3:

```bash
python -c "
import boto3, os
from dotenv import load_dotenv; load_dotenv('.env')
s3 = boto3.client('s3', region_name=os.environ['AWS_DEFAULT_REGION'])
b = 'codegen-deepseek-<account>-<region>'
for o in s3.list_objects_v2(Bucket=b, Prefix='generated-code/').get('Contents', []):
    print(f\"{o['Size']:>6}  {o['LastModified']}  s3://{b}/{o['Key']}\")
"
```

### Inspecting Lambda logs

```bash
aws logs tail /aws/lambda/codegen-deepseek --follow --region ap-south-1
```

---

## Security notes

- `.env` is **gitignored** and should never be committed. Rotate the
  access keys in IAM any time you suspect they've been exposed (shared in
  chat transcripts, screen shares, etc.).
- The bucket is created private with Public Access Block on; default
  encryption is **SSE-S3 (AES256)**. Swap to `aws:kms` with a customer-
  managed KMS key for regulated workloads.
- The API Gateway endpoint is **unauthenticated** (`authorizationType=NONE`)
  for simplicity. Before anything real, add an API key (`apiKeyRequired=True`),
  a Cognito authorizer, or IAM-sig-v4 auth in `infra/api_gateway.py`.
- IAM permissions are scoped to *one bucket prefix* and *one model ARN*.
  If you broaden the model set or target another bucket, update
  `infra/iam.py::_inline_policy`.
- CORS is currently `Access-Control-Allow-Origin: *`. Lock it down to
  your front-end origin in [codegen/lambda_handler.py](codegen/lambda_handler.py).

---

## Common failure modes

| Symptom | Cause | Fix |
|---|---|---|
| `AccessDeniedException` from Bedrock | Model access not enabled | Bedrock console → *Model access* → request DeepSeek V3.2 |
| `ValidationException: on-demand throughput isn't supported` | Model served only via inference profile | Pass `--model-id us.deepseek.v3-2-v1:0` |
| `ResourceConflictException` from IAM on first deploy | Role created in a previous aborted run | Normal — deploy is idempotent; re-run |
| `InvalidParameterValueException: role cannot be assumed` | IAM eventual consistency | Handled by the 10 s sleep in `ensure_role` on first create |
| Remote call returns 500 with `ConfigError` | Lambda missing `S3_BUCKET` env var | Re-run `python deploy.py` |
| `NoSuchBucket` during local run | `.infra-state.json` points at a deleted bucket | Delete `.infra-state.json` or pass `--bucket` |

---

## License

Internal / experimental. No warranty.
