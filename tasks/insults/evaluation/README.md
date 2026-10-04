# Evaluation: Detecting Insults in Social Commentary

[submission.json](submission.json) records the export contract: columns `Insult, Date, Comment`,
probability column `Insult` in [0, 1], one row per public test row, preserving its order.
Copy `Date, Comment` unchanged. Alignment key: `Comment`.

The contract describes the expected output; this file is not a new grader or a submission validator.
MLE-bench supplies the authoritative task-specific scoring implementation inside the external image.
Metric: ROC AUC, higher is better.

After a run, the host-side operator can use the existing research runner:

```bash
bash "$MLEBENCH_AIDE_ROOT/scripts/grade.sh" /absolute/run/submission/submission.csv detecting-insults-in-social-commentary
```

That script invokes `mlebench grade-sample` in the `mleb` container environment and writes
`grade_report.txt` next to the submission directory. `rsi collect` reads that report; it does not implement
or replace the official grader. Private labels must not be exposed to AIDE.

This is a held-out task: score and dynamics never enter improvement memory, and improve is refused.

Process verifiers (leakage indicators, metric mismatch, search health, etc.) are separate and live in
[`rsi/verifiers/`](../../../rsi/verifiers/). They do not replace official scoring.
