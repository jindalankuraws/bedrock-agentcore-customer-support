# Evaluation results

- Batch evaluation: `telco_golden_after_fixes` (`telco_golden_after_fixes-d5b82fc195`)
- Status: **COMPLETED_WITH_ERRORS**
- Dataset: `eval/dataset/telco_golden_v1.jsonl`
- Run at: 2026-10-01 07:15:37
- Raw output: `agent/eval/results/2026-10-01` (gitignored - regenerate by re-running)

- Sessions: 7 completed, 3 failed, 10 total

| Evaluator | Average score | Evaluated | Failed |
|---|---|---|---|
| Builtin.ToolParameterAccuracy | 1.00 | 11 | 5 |
| Builtin.GoalSuccessRate | 1.00 | 8 | 2 |
| Builtin.TrajectoryInOrderMatch | 1.00 | 7 | 3 |
| Builtin.Faithfulness | 1.00 | 10 | 4 |
| Builtin.Helpfulness | 0.80 | 10 | 4 |
| Builtin.ToolSelectionAccuracy | 1.00 | 11 | 5 |
| Builtin.Correctness | 1.00 | 10 | 4 |

## Job errors

- 3 of 10 sessions failed during batch evaluation.

## History

Every run saved under `results/`, oldest first. A score can only be
compared across runs if the same sessions were scored - check the
completed/failed counts before reading a change as an improvement.

| Run | Sessions ok/failed | Correctness | Faithfulness | GoalSuccessRate | Helpfulness | ToolParameterAccuracy | ToolSelectionAccuracy | TrajectoryInOrderMatch |
|---|---|---|---|---|---|---|---|---|
| `telco_golden_20261001_063044` | 0/10 | - | - | - | - | - | - | - |
| `telco_golden_20261001_064336` | 7/3 | 0.86 | 0.91 | 0.88 | 0.76 | 1.00 | 1.00 | 1.00 |
| `telco_golden_final` | 7/3 | 0.86 | 0.89 | 0.88 | 0.74 | 1.00 | 1.00 | 1.00 |
| `telco_golden_after_fixes` | 7/3 | 1.00 | 1.00 | 1.00 | 0.80 | 1.00 | 1.00 | 1.00 |
