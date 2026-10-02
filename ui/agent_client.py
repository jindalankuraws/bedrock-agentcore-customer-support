"""Backend for the chat UI: sends each message to the deployed AgentCore Runtime.

Settings come from environment variables (nothing hardcoded):
  AGENT_RUNTIME_ARN  ARN of the deployed agent (telco-support-runtime stack output,
                     or SSM /telco-support/runtime/arn)
  AGENT_ENDPOINT     runtime endpoint to call (default: live)
  AWS_PROFILE        credentials the UI uses; they need bedrock-agentcore:InvokeAgentRuntime
"""
import json
import os
from typing import Iterator, Optional

import boto3

client = boto3.client("bedrock-agentcore", region_name=os.environ.get("AWS_REGION", "ap-southeast-1"))


def send_message(account_id: Optional[str], session_id: str, prompt: str) -> Iterator[str]:
    """Send one chat message and stream back the reply text."""
    response = client.invoke_agent_runtime(
        agentRuntimeArn=os.environ["AGENT_RUNTIME_ARN"],
        # Which endpoint (a pointer to a version) to call. Rollback = repoint the endpoint; no code change.
        qualifier=os.environ.get("AGENT_ENDPOINT", "live"),
        runtimeSessionId=session_id,  # same id -> same microVM -> the agent remembers the conversation
        # account_id = who is signed in (None for a guest). The agent uses it; the model never sees it.
        payload=json.dumps({"prompt": prompt, "account_id": account_id}),
    )

    # The agent streams Server-Sent Events: one line per chunk, e.g.  data: "Your bill is"
    for line in response["response"].iter_lines(chunk_size=1):
        if line.startswith(b"data: "):
            chunk = json.loads(line[len(b"data: "):])
            yield chunk if isinstance(chunk, str) else f"\n[agent error: {chunk}]"
