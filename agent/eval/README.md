# Evaluation

The agent is evaluated with **AgentCore batch evaluation** over a golden dataset of
**10 scenarios**: the 7 sessions of the original golden set (covering questions q1-q9,
where q1-q3 share one guest session) plus 3 new knowledge-base scenarios. The runner
invokes the deployed agent once per scenario, waits for CloudWatch to ingest the
spans, then asks the service to score the sessions.

## Files

| Path | What it is |
|---|---|
| `dataset/telco_golden_v1.jsonl` | The golden dataset: 10 scenarios, one JSON object per line. |
| `run_batch_eval.py` | The runner. Invokes the agent, submits the batch job, saves results. |
| `RESULTS.md` | Summary table from the most recent run. Generated - do not edit by hand. |
| `results/<date>/` | Raw service output for each run. |
| `golden_set_v23.json`, `golden_set_q9.json` | **Superseded.** The original batch-evaluation request bodies, kept for history. They pin session IDs from a deployment that no longer exists, so they cannot be re-run. |

## Dataset format

One scenario per line ([dataset schema](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/dataset-evaluations-schema.html)):

```json
{"scenario_id":"q5_alex_bill",
 "turns":[{"input":{"prompt":"How much is my latest bill?","account_id":"ACC-1001"}}],
 "assertions":["Gives the bill total of SGD 15.90 ..."],
 "expected_trajectory":["telcoApiGateway___get_bill"]}
```

`turns[].input` is an **object**, not a string, so each scenario can carry the
signed-in customer. Guest scenarios simply omit `account_id`. Turns within a
scenario run sequentially in one session, which is how the two human-in-the-loop
scenarios (`q7`, `q9`) send their "no" / "yes" confirmation.

> **Prompt wording.** The prompts in the dataset are paraphrases of the original
> demo script — for example *"What's my bill?"* became *"How much is my latest
> bill and what am I being charged for?"*. The assertions and expected
> trajectories are the originals. The prompts are kept as they are so that every
> run in [RESULTS.md](RESULTS.md) remains comparable.

> **Measurement conditions.** These runs used a knowledge base on
> `amazon.titan-embed-text-v2:0` in `us-east-1`. The default configuration
> (Cohere Embed Multilingual v3, in region) has not been evaluated, and
> retrieval quality varies between embedding models. See
> [PROJECT_OVERVIEW.md](../../docs/PROJECT_OVERVIEW.md#how-the-results-were-measured).

## Evaluators

All seven are `ACTIVE` in ap-southeast-1 (`aws bedrock-agentcore-control list-evaluators`):

| Evaluator | Level | Ground truth it uses |
|---|---|---|
| `Builtin.GoalSuccessRate` | SESSION | `assertions` |
| `Builtin.TrajectoryInOrderMatch` | SESSION | `expected_trajectory` |
| `Builtin.Correctness` | TRACE | `turns[].expected_response` |
| `Builtin.Faithfulness` | TRACE | - |
| `Builtin.Helpfulness` | TRACE | - |
| `Builtin.ToolSelectionAccuracy` | TOOL_CALL | - |
| `Builtin.ToolParameterAccuracy` | TOOL_CALL | - |

## Running it

Requires a deployed runtime. `platform/deploy-stage2.sh` prints the exact command,
including the log group and service name, as its final output. They are also
stack outputs of `telco-support-runtime` (`RuntimeArn`, `LogGroup`, `ServiceName`).

```bash
cd agent && source .venv/bin/activate

python eval/run_batch_eval.py \
  --runtime-arn  arn:aws:bedrock-agentcore:ap-southeast-1:<account>:runtime/<agent-id> \
  --log-group    /aws/bedrock-agentcore/runtimes/<agent-id>-live \
  --service-name <agent-id>.live \
  --endpoint     live \
  --region       ap-southeast-1
```

Omit `--endpoint` only if the runtime is invoked on `DEFAULT`.

A run takes roughly 5-10 minutes: the scenarios are invoked concurrently, then there
is a fixed 180-second wait for CloudWatch span ingestion, then the job is polled.
Exit code is 0 only when the job reaches `COMPLETED`.

## Why a script rather than the CLI

`agentcore run batch-evaluation --dataset` exists, but it builds its request payload
as `{"prompt": <input>}` with no way to add `account_id`. This agent reads
`account_id` from the payload and refuses account tools without it, so six of the
nine account scenarios would run as a guest and fail. The SDK's
`BatchEvaluationRunner` takes a custom invoker whose `payload` is passed through
unchanged, which is why the runner is Python.

The CLI also reads `expectedResponse` (camelCase) from the dataset file while the
documented field is `expected_response`, so per-turn expected responses are dropped
and `Builtin.Correctness` loses its ground truth. The SDK honours the documented field.
