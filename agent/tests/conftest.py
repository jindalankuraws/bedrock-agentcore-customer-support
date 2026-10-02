"""Shared fixtures for the integration tests.

These run against the DEPLOYED runtime and the DEPLOYED mock API - there are no
mocks here. Configuration is read from the SSM parameters the platform stacks
publish, so the tests need no hard-coded ARNs.

Skipped automatically when the stack is not deployed or credentials are missing.
"""
import json
import os
import urllib.error
import urllib.request
import uuid

import boto3
import pytest
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.exceptions import BotoCoreError, ClientError

REGION = os.environ.get("AWS_REGION", "ap-southeast-1")
SSM_PREFIX = os.environ.get("SSM_PREFIX", "/telco-support")


def _ssm(name, session):
    try:
        return session.client("ssm", region_name=REGION).get_parameter(
            Name=f"{SSM_PREFIX}/{name}")["Parameter"]["Value"]
    except (ClientError, BotoCoreError) as exc:
        pytest.skip(f"{SSM_PREFIX}/{name} unavailable ({exc.__class__.__name__}); stack not deployed?")


@pytest.fixture(scope="session")
def aws():
    return boto3.Session()


@pytest.fixture(scope="session")
def runtime_arn(aws):
    return _ssm("runtime/arn", aws)


@pytest.fixture(scope="session")
def endpoint(aws):
    return _ssm("runtime/endpoint", aws)


@pytest.fixture(scope="session")
def api_base(aws):
    """Base URL of the mock telco API, from the CloudFormation stack outputs."""
    cfn = aws.client("cloudformation", region_name=REGION)
    stack = os.environ.get("MOCK_API_STACK", "mock-telco-api")
    try:
        outs = cfn.describe_stacks(StackName=stack)["Stacks"][0]["Outputs"]
    except (ClientError, BotoCoreError) as exc:
        pytest.skip(f"stack {stack} unavailable ({exc.__class__.__name__})")
    return {o["OutputKey"]: o["OutputValue"] for o in outs}["ApiUrl"]


@pytest.fixture(scope="session")
def call_api(aws, api_base):
    """SigV4-signed call straight to the mock API, bypassing the agent.

    This is how the tests check backend state: if the agent never called
    change_plan, the plan the API reports has not moved.
    """
    creds = aws.get_credentials().get_frozen_credentials()

    def _call(method, path, body=None):
        url = f"{api_base}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = AWSRequest(method=method, url=url, data=data,
                         headers={"Content-Type": "application/json"} if data else {})
        SigV4Auth(creds, "execute-api", REGION).add_auth(req)
        urlreq = urllib.request.Request(url, data=data, method=method,
                                        headers=dict(req.headers))
        try:
            with urllib.request.urlopen(urlreq, timeout=30) as resp:
                return resp.status, json.loads(resp.read() or b"null")
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"null")

    return _call


@pytest.fixture(scope="session")
def ask(aws, runtime_arn, endpoint):
    """Send one turn to the deployed agent and return its text.

    The session id is the caller's: passing the same one continues the
    conversation, which is how a confirmation answer reaches the paused tool.
    """
    client = aws.client("bedrock-agentcore", region_name=REGION)

    def _ask(prompt, session_id, account_id=None):
        payload = {"prompt": prompt}
        if account_id:
            payload["account_id"] = account_id
        resp = client.invoke_agent_runtime(
            agentRuntimeArn=runtime_arn, qualifier=endpoint,
            runtimeSessionId=session_id, payload=json.dumps(payload).encode())
        raw = resp["response"].read().decode()
        chunks = []
        for line in raw.splitlines():
            if not line.startswith("data:"):
                continue
            piece = line[5:]
            chunks.append(json.loads(piece) if piece.strip().startswith('"') else piece)
        return "".join(chunks).strip()

    return _ask


@pytest.fixture
def session_id():
    """A fresh conversation. AgentCore gives each session its own microVM."""
    return str(uuid.uuid4())


@pytest.fixture
def restore_plan(call_api):
    """Put ACC-1001 back on the plan it started on, whatever the test did."""
    status, before = call_api("GET", "/accounts/ACC-1001/plan")
    assert status == 200, f"could not read starting plan: {before}"
    original = before["plan"]["plan_id"]
    yield original
    status, now = call_api("GET", "/accounts/ACC-1001/plan")
    if status == 200 and now["plan"]["plan_id"] != original:
        call_api("POST", "/accounts/ACC-1001/plan-change", {"new_plan_id": original})
