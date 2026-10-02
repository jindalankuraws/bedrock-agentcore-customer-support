# Platform

CloudFormation for the telco support agent. Owned by the platform team.
See [../docs/OWNERSHIP.md](../docs/OWNERSHIP.md) for the contract with the agent team.

## Stacks

| File | Stack | Region | Stage |
|---|---|---|---|
| `10-foundation.yaml` | `telco-support-foundation` | ap-southeast-1 | 1 |
| `20-guardrail.yaml` | `telco-support-guardrail` | ap-southeast-1 | 1 |
| `30-knowledge-base.yaml` | `telco-support-knowledge-base` | ap-southeast-1 | 1 |
| `40-iam.yaml` | `telco-support-iam` | ap-southeast-1 | 1 |
| `50-gateway.yaml` | `telco-support-gateway` | ap-southeast-1 | 1 |
| `60-runtime.yaml` | `telco-support-runtime` | ap-southeast-1 | 2 |
| `70-observability.yaml` | `telco-support-observability` | ap-southeast-1 | 2 |

Every stack deploys into one region. The knowledge base uses Cohere Embed
Multilingual v3, because ap-southeast-1 offers no Amazon embedding model.

To reproduce the variant the published results were measured on:

```bash
KB_REGION=us-east-1 EMBEDDING_MODEL=amazon.titan-embed-text-v2:0 \
  ./platform/deploy-stage1.sh
```

## Running it

```bash
# 0. backend first - the gateway target points at it
cd mock-telco-api && sam build && sam deploy --guided && cd ..

# 1. everything that does not need the agent image
./platform/deploy-stage1.sh

# 2. agent team builds and pushes
./agent/build-and-push.sh

# 3. runtime + observability
ALARM_EMAIL=you@example.com ./platform/deploy-stage2.sh
```

Overridable environment variables: `PROJECT`, `REGION`, `KB_REGION`,
`EMBEDDING_MODEL`, `SSM_PREFIX`, `ENDPOINT_NAME`, `MOCK_API_STACK`, `ALARM_EMAIL`.

## Teardown

```bash
./platform/teardown.sh
```

Empties the S3 and ECR contents first, then deletes the stacks in reverse
dependency order and removes the SSM namespace. It leaves the account budget
in place and prints the command to delete it.
