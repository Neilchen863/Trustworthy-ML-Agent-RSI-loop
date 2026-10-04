# Evaluation: Random Acts of Pizza

[submission.json](submission.json) records the export contract: columns `request_id, requester_received_pizza`,
probability column `requester_received_pizza` in [0, 1], one row per public test row, preserving its order.
Copy `request_id` unchanged. Alignment key: `request_id`.

The contract describes the expected output; this file is not a new grader or a submission validator.
MLE-bench supplies the authoritative task-specific scoring implementation inside the external image.
Metric: ROC AUC, higher is better.

After a run, the host-side operator can use the existing research runner:

```bash
bash "$MLEBENCH_AIDE_ROOT/scripts/grade.sh" /absolute/run/submission/submission.csv random-acts-of-pizza
```

That script invokes `mlebench grade-sample` in the `mleb` container environment and writes
`grade_report.txt` next to the submission directory. `rsi collect` reads that report; it does not implement
or replace the official grader. Private labels must not be exposed to AIDE.

Its post-run official score can enter the outer improver memory; it is not independent evaluation evidence.

Process verifiers (leakage indicators, metric mismatch, search health, etc.) are separate and live in
[`rsi/verifiers/`](../../../rsi/verifiers/). They do not replace official scoring.
