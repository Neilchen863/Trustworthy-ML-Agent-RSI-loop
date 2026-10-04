# Task environments and evaluation

| Task | Role | Input | Metric | Start here |
|---|---|---|---|---|
| Random Acts of Pizza | train | JSON requests | ROC AUC | [Task README](random_acts_of_pizza/README.md) |
| Detecting Insults | test / held-out | CSV comments | ROC AUC | [Task README](insults/README.md) |

Each directory describes one fixed task: `task.json` links the agent instruction, run budget, environment
manifest and submission contract. `environment/` prepares and checks inputs; `evaluation/` explains the
external authoritative grader. Neither is editable by the improver.

Both tasks share [the AIDE base](../environments/aide/README.md). They do not duplicate an image or pretend to
provide task-specific Dockerfiles. AIDE's ML solution code changes within a run; the outer loop changes the
separate harness between runs.

`tests/` at repository root tests the loop implementation. Official task grading is external MLE-bench;
`rsi/verifiers/` adds process checks. These three roles are distinct.
