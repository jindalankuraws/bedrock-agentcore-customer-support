# Ownership and the contract between teams

This repository is laid out the way a two-team enterprise setup would be: a
**platform team** owns the infrastructure, an **agent team** owns the agent.
Neither team can break the other by editing its own files.

> This is a proof of concept built by one person. The split is modelled, not
> staffed: it shows where the boundary sits rather than claiming two real teams.

## Who owns what

| Path | Owner | Contents |
|---|---|---|
| `platform/` | Platform | CloudFormation only. IAM roles, ECR, guardrail, knowledge base, AgentCore Runtime + endpoint, Gateway + target, observability, SSM parameters. |
| `agent/` | Agent | Agent code, system prompt, Dockerfile, dependency lock, evaluation dataset and runner. |
| `kb/` | Agent (content) | The fictional policy documents. Platform owns the knowledge base and the ingestion pipeline; the agent team owns what goes into it. |
| `mock-telco-api/` | Backend | Stands in for the real billing and CRM systems. SAM. |
| `docs/` | Shared | This file, the project overview, evidence. |

The agent team never writes IAM, and never hard-codes an account ID, ARN, bucket
or URL. The platform team never edits a prompt or a tool description, with one
deliberate exception described below.

## The contract

Two artefacts cross the boundary, and nothing else.

**1. The container image.** The agent team builds a `linux/arm64` image and
pushes it to the ECR repository the platform team created. It publishes the
resulting **digest** to SSM. A digest, not a tag, so a later deploy cannot
silently pick up a different build.

**2. SSM parameters** under `/telco-support`. Platform writes, agent reads.

| Parameter | Written by | Read by |
|---|---|---|
| `/ecr/repository-uri` | `10-foundation` | agent build script |
| `/model/id` | `10-foundation` | runtime env var |
| `/guardrail/id`, `/guardrail/version`, `/guardrail/arn` | `20-guardrail` | runtime env var, IAM, dashboard |
| `/kb/id`, `/kb/arn`, `/kb/region` | stage-1 script | runtime env var, IAM |
| `/iam/runtime-role-arn`, `/iam/gateway-role-arn` | `40-iam` | runtime, gateway |
| `/gateway/url`, `/gateway/arn` | `50-gateway` | runtime env var, dashboard |
| `/agent/image-uri` | agent build script | `60-runtime` |
| `/runtime/arn`, `/runtime/endpoint` | `60-runtime` | UI, evaluation runner |

The agent container reads **only environment variables**. CloudFormation resolves
them from SSM with `{{resolve:ssm:...}}` at deploy time. The image is therefore
identical across accounts and environments.

### The deliberate exception: tool descriptions

Tool names and descriptions live in `platform/50-gateway.yaml`, because the
Gateway owns them. They are also the agent's routing logic - changing
`create_ticket`'s description changes agent behaviour. In a real org this is the
classic seam that causes incidents. Two options, both defensible:

- keep them in the gateway template and require an agent-team review on that file
  (what this repo does, via CODEOWNERS in a real setup); or
- move the tool schema to a file the agent team owns and have the platform
  template read it.

Whichever is chosen, it must be explicit. Silent ownership of a tool description
is how an agent regresses with no code change.

## Deployment order

Three phases, because the runtime cannot exist before the image, and the image
cannot be pushed before the repository exists.

```
  backend        mock-telco-api        sam deploy
       |
  stage 1        platform/deploy-stage1.sh
       |         10-foundation   ECR, /model/id
       |         20-guardrail    guardrail + version
       |         30-knowledge-base, upload docs, ingest
       |         40-iam          roles scoped to the guardrail and KB ARNs
       |         50-gateway      gateway + API Gateway target
       |
  build          agent/build-and-push.sh      ARM64 image -> ECR -> /agent/image-uri
       |
  stage 2        platform/deploy-stage2.sh
                 60-runtime      runtime + endpoint (env vars resolved from SSM)
                 70-observability dashboard + tool-error alarm
```

IAM is in stage 1, as it must be - but *after* the guardrail and knowledge base,
so `bedrock:ApplyGuardrail` and `bedrock:Retrieve` can be scoped to those exact
ARNs instead of a wildcard.

The gateway is also in stage 1, although it sits close to the runtime: the
runtime needs its URL as an environment variable, and the gateway itself depends
only on the mock backend.

## Releasing a new agent version

1. Agent team merges a change to `agent/`.
2. CI builds the ARM64 image and pushes it by digest.
3. CI deploys a **new runtime version** - updating `ImageUri` on
   `AWS::BedrockAgentCore::Runtime` creates one. The `live` endpoint keeps
   serving the old version.
4. **Evaluation gate.** `agent/eval/run_batch_eval.py` runs against the new
   version. Promotion is blocked unless the thresholds hold:
   - `Builtin.GoalSuccessRate` at or above the agreed floor,
   - `Builtin.TrajectoryInOrderMatch` no worse than the previous release,
   - zero sessions failing invocation.
   The runner exits non-zero unless the job reaches `COMPLETED`, which is the
   hook CI uses.
5. **Promote** by pointing the endpoint at the new version
   (`AgentRuntimeVersion` on `AWS::BedrockAgentCore::RuntimeEndpoint`).
6. **Roll back** by pointing it at the previous version. The image is still in
   ECR - the lifecycle policy keeps the last 10.

Baselines must be measured before they are enforced. Until a release history
exists, the gate should run and report, not block.

## How this maps to Terraform at a larger org

The staging here is a shell script because it is a proof of concept. At scale the
same boundary becomes modules and separate state:

| Here | Terraform |
|---|---|
| `10-foundation`, `40-iam` | `modules/agent-platform-base` - ECR, IAM, SSM namespace. Own state, changed rarely, platform-team pipeline. |
| `20-guardrail`, `30-knowledge-base` | `modules/agent-guardrail`, `modules/agent-knowledge-base`. The KB module takes the embedding model as a variable; a `providers` alias would express an out-of-region knowledge base if one were ever needed. |
| `50-gateway` | `modules/agent-gateway`, with the tool schema as a variable the agent team supplies. |
| `60-runtime` | `modules/agent-runtime`, taking `image_digest` as its only frequently-changing input. Separate state so an agent release never plans a change to IAM or the KB. |
| `70-observability` | `modules/agent-observability`, fed the runtime and gateway ARNs by remote state. |
| SSM parameters | Either kept (explicit contract, works across tools) or replaced by `terraform_remote_state` outputs. SSM is better when the consumers are not all Terraform - CI, scripts and the container all read it the same way. |
| `deploy-stage1.sh` / `deploy-stage2.sh` | Two pipelines: `platform-apply` and `agent-release`. The ordering constraint becomes a remote-state dependency rather than a script. |

The knowledge base identifiers are written to SSM by the stage-1 script rather
than exported from the stack. In one region that is merely explicit; it becomes
necessary the moment the knowledge base is deployed elsewhere, because
CloudFormation cannot write a parameter into another region. A Terraform provider
alias removes the need entirely.

## Known gaps

- `account_id` is trusted from the UI payload. Production would take it from a
  verified JWT via AgentCore Identity, with the Gateway on `CUSTOM_JWT`.
- Runtime is `PUBLIC`, not VPC mode.
- No customer-managed KMS keys.
- No CODEOWNERS file - the ownership above is documented, not enforced.
- The evaluation gate is defined here but not yet wired into CI.
