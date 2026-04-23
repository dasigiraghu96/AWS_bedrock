"""REST API Gateway wired to the Lambda via AWS_PROXY integration."""

from __future__ import annotations

import logging
import time
from typing import Any

import boto3
from botocore.exceptions import ClientError

log = logging.getLogger("infra.apigw")


def _find_api(client: Any, name: str) -> dict[str, Any] | None:
    paginator = client.get_paginator("get_rest_apis")
    for page in paginator.paginate():
        for api in page.get("items", []):
            if api["name"] == name:
                return api
    return None


def ensure_api(
    session: boto3.session.Session,
    *,
    api_name: str,
    resource_path: str,
    stage: str,
    lambda_arn: str,
    lambda_name: str,
    region: str,
) -> dict[str, str]:
    """Create (or reuse) a REST API with POST /resource_path → Lambda.

    Returns {"api_id": ..., "invoke_url": ..., "stage": ...}.
    """
    apigw = session.client("apigateway", region_name=region)
    lambda_client = session.client("lambda", region_name=region)

    api = _find_api(apigw, api_name)
    if api:
        api_id = api["id"]
        log.info("Reusing REST API %s (%s)", api_name, api_id)
    else:
        log.info("Creating REST API %s", api_name)
        api_id = apigw.create_rest_api(
            name=api_name,
            description="DeepSeek codegen endpoint",
            endpointConfiguration={"types": ["REGIONAL"]},
        )["id"]

    # Find root ("/") and any existing child with the desired path.
    resources = apigw.get_resources(restApiId=api_id, limit=500)["items"]
    root_id = next(r["id"] for r in resources if r["path"] == "/")
    full_path = "/" + resource_path.strip("/")
    existing = next((r for r in resources if r["path"] == full_path), None)

    if existing:
        resource_id = existing["id"]
        log.info("Reusing resource %s (%s)", full_path, resource_id)
    else:
        log.info("Creating resource %s", full_path)
        resource_id = apigw.create_resource(
            restApiId=api_id, parentId=root_id, pathPart=resource_path.strip("/")
        )["id"]

    # POST method.
    try:
        apigw.get_method(restApiId=api_id, resourceId=resource_id, httpMethod="POST")
        log.info("POST method already present")
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "NotFoundException":
            raise
        apigw.put_method(
            restApiId=api_id,
            resourceId=resource_id,
            httpMethod="POST",
            authorizationType="NONE",
            apiKeyRequired=False,
        )
        apigw.put_method_response(
            restApiId=api_id,
            resourceId=resource_id,
            httpMethod="POST",
            statusCode="200",
            responseModels={"application/json": "Empty"},
        )

    # AWS_PROXY integration to the Lambda.
    invocation_uri = (
        f"arn:aws:apigateway:{region}:lambda:path/2015-03-31/functions/"
        f"{lambda_arn}/invocations"
    )
    apigw.put_integration(
        restApiId=api_id,
        resourceId=resource_id,
        httpMethod="POST",
        type="AWS_PROXY",
        integrationHttpMethod="POST",
        uri=invocation_uri,
    )
    apigw.put_integration_response(
        restApiId=api_id,
        resourceId=resource_id,
        httpMethod="POST",
        statusCode="200",
    )

    # Permit API Gateway to invoke the Lambda. Idempotent add with a stable
    # statement id; swallow ResourceConflictException on re-deploy.
    account_id = session.client("sts").get_caller_identity()["Account"]
    source_arn = (
        f"arn:aws:execute-api:{region}:{account_id}:{api_id}/*/POST/{resource_path.strip('/')}"
    )
    try:
        lambda_client.add_permission(
            FunctionName=lambda_name,
            StatementId=f"apigw-invoke-{api_id}",
            Action="lambda:InvokeFunction",
            Principal="apigateway.amazonaws.com",
            SourceArn=source_arn,
        )
        log.info("Granted API Gateway invoke permission on %s", lambda_name)
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ResourceConflictException":
            raise

    # Deploy to the stage.
    log.info("Deploying to stage %s", stage)
    apigw.create_deployment(restApiId=api_id, stageName=stage)

    # Small eventual-consistency pause before the first call.
    time.sleep(2)

    invoke_url = (
        f"https://{api_id}.execute-api.{region}.amazonaws.com/{stage}/{resource_path.strip('/')}"
    )
    return {"api_id": api_id, "invoke_url": invoke_url, "stage": stage}


def delete_api(session: boto3.session.Session, api_id: str, region: str) -> None:
    apigw = session.client("apigateway", region_name=region)
    try:
        apigw.delete_rest_api(restApiId=api_id)
        log.info("Deleted REST API %s", api_id)
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "NotFoundException":
            log.info("REST API %s already gone", api_id)
            return
        raise
