# Detecting Insults in Social Commentary

Role: **test**. Metric: **ROC AUC**, higher is better. Run configuration is in
[task.json](task.json): agent mode, 25 steps, 2 hours, 8 CPUs and no GPU.

## Follow the task

1. [Instruction](instruction.md): task meaning and information available to the agent.
2. [Environment manifest](environment/manifest.json): shared image reference, data paths and preparation entry.
3. [Shared AIDE environment](../../environments/aide/README.md): runner, image, overlay and external dependencies.
4. [Evaluation](evaluation/README.md): output contract and official scoring.

```text
task.json → environment/manifest.json → environments/aide/manifest.json
          → evaluation/submission.json → external MLE-bench grader
```

## Prepare and check (from repository root)

```bash
# Read-only metadata check; works without CRC or data.
python tasks/insults/environment/check.py --metadata-only

# Set MLEBENCH_AIDE_ROOT, DATA_DIR, SIF_PATH and RSI_OVERLAY_PATH to the
# same resolved settings used by the external research runner.
bash tasks/insults/environment/prepare.sh
python tasks/insults/environment/check.py
```

Preparation invokes the external runner and may download data; it needs accepted Kaggle rules and credentials
configured outside this repository. Preflight checks paths and available commands; it does not execute the
container, prove dependency compatibility or read private labels. It reports missing prerequisites explicitly.

Public inputs: `train.csv` and `test.csv` under
`$DATA_DIR/detecting-insults-in-social-commentary/prepared/public/`. The grader alone uses `prepared/private/`.

To run a published harness, follow [the loop commands](../../docs/operations.md), selecting
`--task insults`. Environment and evaluation files are fixed method inputs, never improver outputs.
