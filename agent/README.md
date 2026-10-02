# Agent

The Strands agent, its prompts, its container image, and the evaluation and tests
that verify its behaviour. Owned by the agent team — see
[../docs/OWNERSHIP.md](../docs/OWNERSHIP.md) for the boundary with `platform/`.

This directory contains **no infrastructure and no account-specific
configuration**. Everything the agent needs arrives as environment variables,
which CloudFormation resolves from SSM at deploy time, so the image is identical
across accounts and environments.

## Layout

```
src/                 what ships in the container
  main.py            BedrockAgentCoreApp entrypoint, system prompt, agent wiring
  model/load.py      Nova via BedrockModel, with the guardrail attached
  mcp_client/        MCP client for the AgentCore Gateway (SigV4)
  policy_tool.py     search_policy — Bedrock Retrieve over the knowledge base
  hooks/identity.py  AccountBinding — forces the signed-in account onto tool calls
  hooks/approval.py  DeclinedToolMessage, ToolCallLogger
  thinking_filter.py strips <thinking> blocks out of the stream
Dockerfile           linux/arm64 image; opentelemetry-instrument entrypoint
build-and-push.sh    build, push by digest, publish the URI to SSM
eval/                golden dataset, batch-evaluation runner, results, analysis
tests/               human-in-the-loop integration tests
```

## Environment variables

Set by `platform/60-runtime.yaml` from SSM. For local work, copy
[../.env.example](../.env.example).

| Variable | Required | Description |
|---|---|---|
| `MODEL_ID` | no | Bedrock model. Defaults to `apac.amazon.nova-pro-v1:0`. |
| `GUARDRAIL_ID` / `GUARDRAIL_VERSION` | no | Omit to run without a guardrail. |
| `KB_ID` | yes for `search_policy` | Knowledge base to retrieve from. |
| `KB_REGION` | no | Defaults to the runtime's own region. |
| `AGENTCORE_GATEWAY_TELCO_GATEWAY_URL` | yes | Gateway MCP endpoint; without it the account tools are unavailable and the agent says so. |

## Working on it

```bash
uv sync                                  # create .venv from the lock file
.venv/bin/python -m pytest tests/ -v     # human-in-the-loop tests (needs a deployment)
```

The tests run against the **deployed** runtime and the deployed mock API — there
are no mocks. They skip themselves if the stack is not deployed or credentials
are missing.

## Releasing

```bash
./build-and-push.sh                      # linux/arm64 → ECR, pinned by digest
../platform/deploy-stage2.sh             # new runtime version + endpoint
```

A release is gated on the evaluation, not on the build succeeding — see
[eval/README.md](eval/README.md) and the release section of
[../docs/OWNERSHIP.md](../docs/OWNERSHIP.md).
