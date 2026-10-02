#!/usr/bin/env python3
"""Run an AgentCore batch evaluation over the golden dataset.

Why a Python script and not `agentcore run batch-evaluation --dataset`:
the CLI builds its payload as {"prompt": <turn input>} and offers no way to add
account_id. This agent reads account_id from the payload and refuses account tools
without it, so every signed-in scenario would run as a guest and fail. The SDK
runner takes a custom invoker, where AgentInvokerInput.payload is passed through
untouched, so a turn input can be a dict. See:
  https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/dataset-evaluations-batch.html
  https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/dataset-evaluations-schema.html

Usage:
  python eval/run_batch_eval.py --runtime-arn <arn> --log-group <lg> --service-name <svc>

Any argument can instead come from the environment:
  AGENT_RUNTIME_ARN, EVAL_LOG_GROUP, EVAL_SERVICE_NAME, AWS_REGION
"""
import argparse
import json
import os
import pathlib
import sys
from datetime import datetime, timezone

import boto3
from bedrock_agentcore.evaluation import (
    AgentInvokerInput,
    AgentInvokerOutput,
    BatchEvaluationRunConfig,
    BatchEvaluationRunner,
    BatchEvaluatorConfig,
    CloudWatchDataSourceConfig,
    FileDatasetProvider,
)

HERE = pathlib.Path(__file__).parent
DEFAULT_DATASET = HERE / "dataset" / "telco_golden_v1.jsonl"

# All verified ACTIVE in ap-southeast-1 with:
#   aws bedrock-agentcore-control list-evaluators --region ap-southeast-1
EVALUATORS = [
    "Builtin.GoalSuccessRate",          # SESSION  - uses `assertions`
    "Builtin.TrajectoryInOrderMatch",   # SESSION  - uses `expected_trajectory`
    "Builtin.Correctness",              # TRACE
    "Builtin.Faithfulness",             # TRACE
    "Builtin.Helpfulness",              # TRACE
    "Builtin.ToolSelectionAccuracy",    # TOOL_CALL
    "Builtin.ToolParameterAccuracy",    # TOOL_CALL
]


def build_invoker(runtime_arn: str, region: str, endpoint: str | None):
    """Return a thread-safe invoker. One boto3 client is shared; boto3 clients are thread-safe."""
    client = boto3.client("bedrock-agentcore", region_name=region)

    def agent_invoker(invoker_input: AgentInvokerInput) -> AgentInvokerOutput:
        payload = invoker_input.payload
        if isinstance(payload, str):          # plain prompt, no signed-in customer
            payload = {"prompt": payload}
        body = json.dumps(payload).encode()

        kwargs = {
            "agentRuntimeArn": runtime_arn,
            "runtimeSessionId": invoker_input.session_id,
            "payload": body,
        }
        if endpoint:
            kwargs["qualifier"] = endpoint

        response = client.invoke_agent_runtime(**kwargs)
        raw = response["response"].read().decode()

        # main.py streams text chunks; depending on the Accept header this comes back
        # as SSE ("data: ..." lines) or as a plain body. Keep whatever we get.
        if raw.startswith("data:"):
            text = "".join(
                line[len("data:"):].strip()
                for line in raw.splitlines()
                if line.startswith("data:")
            )
        else:
            text = raw
        try:
            return AgentInvokerOutput(agent_output=json.loads(text))
        except json.JSONDecodeError:
            return AgentInvokerOutput(agent_output=text)

    return agent_invoker


def summarise(result, dataset_path, out_dir) -> str:
    """Build the Markdown summary table. Only real numbers from the service."""
    lines = [
        "# Evaluation results",
        "",
        f"- Batch evaluation: `{result.batch_evaluation_name}` (`{result.batch_evaluation_id}`)",
        f"- Status: **{result.status}**",
        f"- Dataset: `{dataset_path}`",
        # The job's own creation time, not now(): this file is regenerated from a
        # saved result occasionally, and the timestamp must keep describing the run.
        f"- Run at: {str(result.created_at or '')[:19]}",
        f"- Raw output: `{out_dir}` (gitignored - regenerate by re-running)",
        "",
    ]

    summary = result.evaluation_results
    if summary:
        lines += [
            f"- Sessions: {summary.number_of_sessions_completed} completed, "
            f"{summary.number_of_sessions_failed} failed, "
            f"{summary.total_number_of_sessions} total",
            "",
            "| Evaluator | Average score | Evaluated | Failed |",
            "|---|---|---|---|",
        ]
        for ev in summary.evaluator_summaries or []:
            avg = ev.statistics.average_score if ev.statistics else None
            avg_text = f"{avg:.2f}" if isinstance(avg, (int, float)) else "n/a"
            lines.append(f"| {ev.evaluator_id} | {avg_text} | {ev.total_evaluated} | {ev.total_failed} |")
        lines.append("")

    if result.agent_invocation_failures:
        lines += ["## Scenarios that failed before evaluation", ""]
        for f in result.agent_invocation_failures:
            lines.append(f"- `{f.scenario_id}`: {getattr(f, 'error', 'invocation failed')}")
        lines.append("")

    if result.error_details:
        lines += ["## Job errors", ""] + [f"- {e}" for e in result.error_details] + [""]

    history = _history_table(out_dir.parent)
    if history:
        lines += ["## History", "",
                  "Every run saved under `results/`, oldest first. A score can only be",
                  "compared across runs if the same sessions were scored - check the",
                  "completed/failed counts before reading a change as an improvement.",
                  ""] + history + [""]

    return "\n".join(lines)


def _history_table(results_root: pathlib.Path) -> list[str]:
    """Build a run-over-run table from every saved result file."""
    runs = []
    for path in sorted(results_root.glob("*/*.json")):
        if path.name.endswith("-events.json"):
            continue
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        summary = data.get("evaluation_results") or {}
        scores = {
            s.get("evaluator_id", "").replace("Builtin.", ""):
                (s.get("statistics") or {}).get("average_score")
            for s in (summary.get("evaluator_summaries") or [])
        }
        if not scores:
            continue
        runs.append({
            "name": data.get("batch_evaluation_name") or path.stem,
            "when": str(data.get("created_at", ""))[:19],
            "ok": summary.get("number_of_sessions_completed"),
            "failed": summary.get("number_of_sessions_failed"),
            "scores": scores,
        })
    if len(runs) < 2:
        return []
    runs.sort(key=lambda r: r["when"])   # chronological, not by filename

    names = sorted({k for r in runs for k in r["scores"]})
    out = ["| Run | Sessions ok/failed | " + " | ".join(names) + " |",
           "|---" * (len(names) + 2) + "|"]
    for r in runs:
        cells = []
        for n in names:
            v = r["scores"].get(n)
            cells.append(f"{v:.2f}" if isinstance(v, (int, float)) else "-")
        out.append(f"| `{r['name']}` | {r['ok']}/{r['failed']} | " + " | ".join(cells) + " |")
    return out


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--runtime-arn", default=os.environ.get("AGENT_RUNTIME_ARN"))
    p.add_argument("--log-group", default=os.environ.get("EVAL_LOG_GROUP"),
                   help="/aws/bedrock-agentcore/runtimes/<agent-id>-<endpoint>")
    p.add_argument("--service-name", default=os.environ.get("EVAL_SERVICE_NAME"),
                   help="<agent-id>.<endpoint>")
    p.add_argument("--endpoint", default=os.environ.get("AGENT_ENDPOINT"),
                   help="Runtime endpoint qualifier. Omit for DEFAULT.")
    p.add_argument("--region", default=os.environ.get("AWS_REGION", "ap-southeast-1"))
    p.add_argument("--dataset", default=str(DEFAULT_DATASET))
    p.add_argument("--name", default=None, help="Batch evaluation name.")
    p.add_argument("--ingestion-delay", type=int, default=180,
                   help="Seconds to wait for CloudWatch to ingest spans (service default 180).")
    p.add_argument("--timeout", type=int, default=1800)
    args = p.parse_args()

    missing = [n for n, v in
               [("--runtime-arn", args.runtime_arn), ("--log-group", args.log_group),
                ("--service-name", args.service_name)] if not v]
    if missing:
        p.error("missing required argument(s): " + ", ".join(missing))

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_dir = HERE / "results" / stamp
    out_dir.mkdir(parents=True, exist_ok=True)

    dataset = FileDatasetProvider(args.dataset).get_dataset()
    print(f"Loaded {len(dataset.scenarios)} scenarios from {args.dataset}")

    # The service requires [a-zA-Z][a-zA-Z0-9_]{0,47} - no hyphens.
    name = args.name or f"telco_golden_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    config = BatchEvaluationRunConfig(
        batch_evaluation_name=name,
        evaluator_config=BatchEvaluatorConfig(evaluator_ids=EVALUATORS),
        data_source=CloudWatchDataSourceConfig(
            service_names=[args.service_name],
            log_group_names=[args.log_group],
            ingestion_delay_seconds=args.ingestion_delay,
        ),
        polling_timeout_seconds=args.timeout,
        polling_interval_seconds=30,
    )

    runner = BatchEvaluationRunner(region=args.region)
    result = runner.run_dataset_evaluation(
        agent_invoker=build_invoker(args.runtime_arn, args.region, args.endpoint),
        dataset=dataset,
        config=config,
    )

    (out_dir / f"{name}.json").write_text(
        json.dumps(result.model_dump(mode="json"), indent=2, default=str)
    )

    # Per-session, per-evaluator detail with explanations.
    if result.output_data_config:
        try:
            events = runner.fetch_evaluation_events(result)
            (out_dir / f"{name}-events.json").write_text(json.dumps(events, indent=2, default=str))
            print(f"Saved {len(events)} per-session evaluation events")
        except Exception as exc:                       # noqa: BLE001 - detail is a bonus, not the result
            print(f"Could not fetch per-session events: {exc}", file=sys.stderr)

    report = summarise(result, args.dataset, out_dir)
    (HERE / "RESULTS.md").write_text(report)
    print("\n" + report)

    return 0 if result.status == "COMPLETED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
