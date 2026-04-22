Good choices. I'll recommend **AWS SAM** for your case (reasoning at the end if you're curious). Here's the complete step-by-step plan.

## The plan at a glance

```
Local:                GitHub:                AWS:
                                          
codegen-bedrock/  ─→  main branch  ─→      dev stack (auto)
                      staging branch ─→    staging stack (auto)
                      prod tag v1.0.0 ─→   prod stack (with approval)
```

Three separate CloudFormation stacks, three separate S3 buckets, three separate API Gateway URLs. Isolated blast radius.

I'll break this into 7 steps. Do them in order.

---

## Step 1: Install tools locally

You need three CLIs installed on your machine.

```bash
# AWS CLI
# macOS: brew install awscli
# Linux: curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o awscli.zip && unzip awscli.zip && sudo ./aws/install
# Windows: download from https://aws.amazon.com/cli/

# SAM CLI
# macOS: brew install aws-sam-cli
# Linux/Windows: https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html

# Git (you probably have this)
git --version
```

Verify:
```bash
aws --version        # aws-cli/2.x
sam --version        # SAM CLI, version 1.x
```

Configure AWS CLI with credentials:
```bash
aws configure
# AWS Access Key ID: <your access key>
# AWS Secret Access Key: <your secret>
# Default region: ap-south-1  (or whichever you're using)
# Default output format: json
```

## Step 2: Set up the repo structure locally

```bash
mkdir codegen-bedrock && cd codegen-bedrock

# Create folders
mkdir -p src tests .github/workflows

# Create empty files
touch src/lambda_function.py
touch template.yaml
touch samconfig.toml
touch requirements.txt
touch tests/test_lambda.py
touch .github/workflows/deploy.yml
touch .gitignore
touch README.md
```

Fill in `.gitignore`:

```gitignore
# Python
__pycache__/
*.py[cod]
.pytest_cache/
.venv/
venv/

# SAM
.aws-sam/
samconfig.local.toml

# Secrets
.env
*.pem
```

Fill in `requirements.txt` (boto3 is in the Lambda runtime but pinning it helps local testing):

```txt
boto3==1.35.36
```

Copy your existing Lambda code into `src/lambda_function.py` — the exact same file you have in the AWS console today.

## Step 3: Write the SAM template

This single file replaces every console click you made. Create `template.yaml`:

```yaml
AWSTemplateFormatVersion: '2010-09-09'
Transform: AWS::Serverless-2016-10-31
Description: >
  Code generation API using Amazon Bedrock (DeepSeek V3.2).
  Lambda + API Gateway (HTTP API) + S3 for generated code storage.

# -----------------------------------------------------------------------------
# Parameters: change per environment (dev, staging, prod)
# -----------------------------------------------------------------------------
Parameters:
  Environment:
    Type: String
    AllowedValues: [dev, staging, prod]
    Description: Deployment environment name.
  BedrockRegion:
    Type: String
    Default: ap-south-1
    Description: AWS region where DeepSeek V3.2 is invoked.
  ModelId:
    Type: String
    Default: deepseek.v3.2

# -----------------------------------------------------------------------------
# Global defaults applied to all Lambda functions
# -----------------------------------------------------------------------------
Globals:
  Function:
    Runtime: python3.12
    Timeout: 60
    MemorySize: 512
    Architectures: [arm64]  # cheaper and faster than x86_64
    Tracing: Active         # X-Ray tracing for debugging

# -----------------------------------------------------------------------------
# Resources: the AWS things we create
# -----------------------------------------------------------------------------
Resources:

  # --- S3 bucket for generated code ----------------------------------------
  GeneratedCodeBucket:
    Type: AWS::S3::Bucket
    Properties:
      BucketName: !Sub codegen-deepseek-${Environment}-${AWS::AccountId}
      PublicAccessBlockConfiguration:
        BlockPublicAcls: true
        BlockPublicPolicy: true
        IgnorePublicAcls: true
        RestrictPublicBuckets: true
      BucketEncryption:
        ServerSideEncryptionConfiguration:
          - ServerSideEncryptionByDefault:
              SSEAlgorithm: AES256
      LifecycleConfiguration:
        Rules:
          - Id: ExpireOldGenerations
            Status: Enabled
            ExpirationInDays: 90  # auto-delete after 90 days

  # --- Lambda function ------------------------------------------------------
  CodeGenFunction:
    Type: AWS::Serverless::Function
    Properties:
      FunctionName: !Sub codegen-${Environment}
      CodeUri: src/
      Handler: lambda_function.lambda_handler
      Environment:
        Variables:
          BEDROCK_REGION: !Ref BedrockRegion
          DEFAULT_MODEL_ID: !Ref ModelId
          S3_BUCKET: !Ref GeneratedCodeBucket
          S3_PREFIX: generated-code
          MAX_PROMPT_CHARS: "8000"
      Policies:
        - Statement:
            - Sid: BedrockInvoke
              Effect: Allow
              Action:
                - bedrock:InvokeModel
                - bedrock:Converse
              Resource: !Sub arn:aws:bedrock:*::foundation-model/${ModelId}
        - S3WritePolicy:
            BucketName: !Ref GeneratedCodeBucket
      Events:
        ApiEvent:
          Type: HttpApi
          Properties:
            ApiId: !Ref CodeGenApi
            Path: /generate
            Method: POST

  # --- API Gateway (HTTP API) ----------------------------------------------
  CodeGenApi:
    Type: AWS::Serverless::HttpApi
    Properties:
      StageName: !Ref Environment
      CorsConfiguration:
        AllowOrigins: ['*']        # tighten in prod later
        AllowMethods: [POST, OPTIONS]
        AllowHeaders: [Content-Type]

  # --- CloudWatch log group with retention ---------------------------------
  CodeGenFunctionLogGroup:
    Type: AWS::Logs::LogGroup
    Properties:
      LogGroupName: !Sub /aws/lambda/codegen-${Environment}
      RetentionInDays: 14  # don't keep logs forever

# -----------------------------------------------------------------------------
# Outputs: useful values printed after deployment
# -----------------------------------------------------------------------------
Outputs:
  ApiUrl:
    Description: Invoke URL for the code generation API
    Value: !Sub https://${CodeGenApi}.execute-api.${AWS::Region}.amazonaws.com/${Environment}/generate
  BucketName:
    Value: !Ref GeneratedCodeBucket
  FunctionName:
    Value: !Ref CodeGenFunction
```

## Step 4: Deploy manually once to each environment (bootstrap)

Before GitHub Actions can deploy, you need to create the initial stacks. This also lets you confirm the template works.

```bash
# Build the SAM app (packages code + resolves the template)
sam build

# Deploy to dev — guided mode will ask questions the first time
sam deploy --guided \
  --stack-name codegen-bedrock-dev \
  --parameter-overrides Environment=dev \
  --capabilities CAPABILITY_IAM \
  --region ap-south-1 \
  --resolve-s3 \
  --config-env dev
```

When it asks:
- **Confirm changes before deploy:** `N` (faster iteration; keep `Y` for prod)
- **Allow SAM CLI IAM role creation:** `Y`
- **Save arguments to configuration file:** `Y`
- **SAM configuration file:** press Enter (defaults to `samconfig.toml`)
- **SAM configuration environment:** `dev`

After ~2 minutes, it prints the API URL. Test it:

```bash
curl -X POST <ApiUrl from output> \
  -H "Content-Type: application/json" \
  -d '{"prompt":"hello world in python","language":"python"}'
```

Now deploy staging and prod the same way (the config is saved so it's one command each):

```bash
sam deploy --config-env staging \
  --stack-name codegen-bedrock-staging \
  --parameter-overrides Environment=staging

sam deploy --config-env prod \
  --stack-name codegen-bedrock-prod \
  --parameter-overrides Environment=prod
```

You now have three fully isolated stacks running in parallel. Verify in the CloudFormation console.

## Step 5: Set up GitHub OIDC for keyless AWS deploys

This is the modern way — no long-lived AWS keys stored in GitHub. GitHub requests temporary credentials from AWS at deploy time.

**In AWS Console (one-time, do this via CLI to be IaC-consistent):**

Create a file `github-oidc.yaml`:

```yaml
AWSTemplateFormatVersion: '2010-09-09'
Description: OIDC provider + deploy role for GitHub Actions

Parameters:
  GitHubOrg:
    Type: String
    Description: Your GitHub username or org (e.g. "yourname")
  RepoName:
    Type: String
    Default: codegen-bedrock

Resources:
  GitHubOIDCProvider:
    Type: AWS::IAM::OIDCProvider
    Properties:
      Url: https://token.actions.githubusercontent.com
      ClientIdList: [sts.amazonaws.com]
      ThumbprintList: [6938fd4d98bab03faadb97b34396831e3780aea1]

  GitHubDeployRole:
    Type: AWS::IAM::Role
    Properties:
      RoleName: GitHubActionsDeployRole
      AssumeRolePolicyDocument:
        Version: '2012-10-17'
        Statement:
          - Effect: Allow
            Principal:
              Federated: !Ref GitHubOIDCProvider
            Action: sts:AssumeRoleWithWebIdentity
            Condition:
              StringEquals:
                token.actions.githubusercontent.com:aud: sts.amazonaws.com
              StringLike:
                token.actions.githubusercontent.com:sub: !Sub repo:${GitHubOrg}/${RepoName}:*
      ManagedPolicyArns:
        # Broad for simplicity — tighten for real prod
        - arn:aws:iam::aws:policy/PowerUserAccess
        - arn:aws:iam::aws:policy/IAMFullAccess

Outputs:
  RoleArn:
    Value: !GetAtt GitHubDeployRole.Arn
```

Deploy it:
```bash
aws cloudformation deploy \
  --template-file github-oidc.yaml \
  --stack-name github-oidc-setup \
  --parameter-overrides GitHubOrg=YOUR_GITHUB_USERNAME \
  --capabilities CAPABILITY_NAMED_IAM
```

Copy the `RoleArn` output — you'll need it.

## Step 6: Write the GitHub Actions workflow

Create `.github/workflows/deploy.yml`:

```yaml
name: Deploy

on:
  push:
    branches: [main, staging]
  release:
    types: [published]  # prod deploy triggered by GitHub release

# OIDC needs this
permissions:
  id-token: write
  contents: read

jobs:
  # -----------------------------------------------------------------
  # Test: run on every push and PR
  # -----------------------------------------------------------------
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - run: pip install -r requirements.txt pytest
      - run: pytest tests/ -v
        continue-on-error: true  # remove once you have real tests

  # -----------------------------------------------------------------
  # Deploy to dev: triggered by push to main
  # -----------------------------------------------------------------
  deploy-dev:
    needs: test
    if: github.ref == 'refs/heads/main'
    runs-on: ubuntu-latest
    environment: dev
    steps:
      - uses: actions/checkout@v4
      - uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: arn:aws:iam::YOUR_ACCOUNT_ID:role/GitHubActionsDeployRole
          aws-region: ap-south-1
      - uses: aws-actions/setup-sam@v2
      - run: sam build
      - run: |
          sam deploy --config-env dev \
            --stack-name codegen-bedrock-dev \
            --parameter-overrides Environment=dev \
            --no-confirm-changeset \
            --no-fail-on-empty-changeset

  # -----------------------------------------------------------------
  # Deploy to staging: triggered by push to staging branch
  # -----------------------------------------------------------------
  deploy-staging:
    needs: test
    if: github.ref == 'refs/heads/staging'
    runs-on: ubuntu-latest
    environment: staging
    steps:
      - uses: actions/checkout@v4
      - uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: arn:aws:iam::YOUR_ACCOUNT_ID:role/GitHubActionsDeployRole
          aws-region: ap-south-1
      - uses: aws-actions/setup-sam@v2
      - run: sam build
      - run: |
          sam deploy --config-env staging \
            --stack-name codegen-bedrock-staging \
            --parameter-overrides Environment=staging \
            --no-confirm-changeset \
            --no-fail-on-empty-changeset

  # -----------------------------------------------------------------
  # Deploy to prod: triggered by a GitHub release (manual approval)
  # -----------------------------------------------------------------
  deploy-prod:
    needs: test
    if: github.event_name == 'release'
    runs-on: ubuntu-latest
    environment: prod  # GitHub environment with required reviewers
    steps:
      - uses: actions/checkout@v4
      - uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: arn:aws:iam::YOUR_ACCOUNT_ID:role/GitHubActionsDeployRole
          aws-region: ap-south-1
      - uses: aws-actions/setup-sam@v2
      - run: sam build
      - run: |
          sam deploy --config-env prod \
            --stack-name codegen-bedrock-prod \
            --parameter-overrides Environment=prod \
            --no-confirm-changeset \
            --no-fail-on-empty-changeset
```

Replace `YOUR_ACCOUNT_ID` with your 12-digit AWS account ID in all three places.

**Set up GitHub Environments for approval gates:**

GitHub repo → **Settings** → **Environments** → **New environment**:
- Create `dev`, `staging`, `prod`
- On `prod`: enable **Required reviewers** and add yourself. This means prod deploys wait for your manual click.

## Step 7: Commit, push, watch it deploy

```bash
cd codegen-bedrock
git init
git add .
git commit -m "Initial IaC setup"

# Create repo on GitHub (via web or gh CLI), then:
git remote add origin https://github.com/YOUR_USERNAME/codegen-bedrock.git
git branch -M main
git push -u origin main
```

Go to your repo → **Actions** tab → watch the workflow run. Dev should deploy automatically in ~3 minutes.

**Deploy to staging:**
```bash
git checkout -b staging
git push -u origin staging
```

**Deploy to prod:**
- GitHub repo → **Releases** → **Draft a new release** → tag `v1.0.0` → **Publish**
- GitHub Actions starts the prod job → waits for your approval → you click approve → deploys

---

## Your day-to-day workflow from now on

```bash
# Make changes
vim src/lambda_function.py

# Test locally (SAM can invoke your Lambda locally!)
sam local invoke CodeGenFunction -e events/test-event.json

# Commit and push to main → dev updates automatically
git add . && git commit -m "feat: add foo" && git push

# Happy with dev? Merge to staging
git checkout staging && git merge main && git push

# Happy with staging? Create a release → prod (with approval)
```

**Never touch the AWS console again** for this project. Every change goes through git.

---

## Cleaning up the manual setup

Once the SAM stacks are working, delete the resources you created manually in earlier steps (otherwise you'll have duplicates costing money):

1. AWS Console → Lambda → delete your manually-created function
2. S3 → empty and delete your manually-created bucket
3. API Gateway → delete your manually-created HTTP API
4. IAM → delete any manually-created inline policies

The SAM-managed versions (named `codegen-dev`, `codegen-staging`, `codegen-prod`) take over.

---

## Why SAM over Terraform / CDK (for your case)

- **You're AWS-only.** Terraform's multi-cloud is wasted on you.
- **Lambda-specific features.** SAM has first-class support for Lambda layers, local invocation (`sam local`), API Gateway integration that "just works." Terraform requires more wiring.
- **CDK is great** but Python-in-Python is overkill when your infra is 50 lines of YAML. CDK shines when infra gets complex (100+ resources, shared abstractions across teams).
- **Ubiquity for this use case.** AWS docs and tutorials for Lambda overwhelmingly use SAM. Easier to find help.

If your infra grows past ~20 resources or you need to manage non-AWS things (Cloudflare DNS, Datadog monitors, etc.), migrate to Terraform later — the SAM knowledge translates directly.

---

That's the full setup. Let me know which step you want to start with and I'll help you through it. Would recommend doing Step 1 (install tools) + Step 2 (repo structure) first, then we can verify before moving on.
