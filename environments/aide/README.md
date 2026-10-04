# Shared AIDE execution environment

Both [ROAP](../../tasks/random_acts_of_pizza/README.md) and [insults](../../tasks/insults/README.md) reference
[manifest.json](manifest.json). Task-specific public data and submission contracts live with each task.
The harness is versioned separately under `harness/H*/`; the improver cannot edit environment definitions.

## What exists, and what is external

| Component | Location / source | Version status |
|---|---|---|
| Loop and fixed hook adapter | This repository (`rsi/`) | Git commit and method digest |
| Overlay build recipe | [build_overlay.sh](build_overlay.sh) | Included in method freeze |
| Research runner / AIDE integration | `$MLEBENCH_AIDE_ROOT` | External checkout; exact deployed revision must be recorded |
| Apptainer image with `agent` and `mleb` conda environments | `$SIF_PATH` | External image; no current hash supplied in this repo |
| Base v3 overlay (metric-parser and decision fixes) | `$SRC_OVERLAY` | External prerequisite, not built by this repo |
| Resulting overlay with current fixed adapter | `$DST_OVERLAY`, then `$RSI_OVERLAY_PATH` | SHA-256 recorded by `rsi freeze --overlay` |
| Prepared task data | `$DATA_DIR/<competition>/prepared/` | Prepared externally with MLE-bench; no dataset hashes supplied here |

Null revision/hash fields in the manifest mean **not pinned**, not “latest verified”. This repo is not a
standalone Docker environment. The historical accepted overlay hash is in the evidence pack; it is not the
hash of an overlay rebuilt from current code. Before claiming exact reproduction, record the deployed runner
commit, AIDE/MLE-bench revisions, image and data hashes along with the run. The current freeze checks repository
protocols and the selected overlay, not all those external artifacts.

## Setup flow

1. Provide a compatible MLE-bench_AIDE research checkout, Apptainer image and fixed v3 base overlay.
2. Configure the same resolved paths in the external runner and your shell (`MLEBENCH_AIDE_ROOT`, `DATA_DIR`,
   `SIF_PATH`). Its `_common.sh` loads its own configuration; exporting these for preflight does not replace that.
3. Prepare each task via `tasks/<task>/environment/prepare.sh`; that delegates to `scripts/02_prepare_data.sh`.
4. Build a **new** overlay destination on a CRC compute node:

```bash
export AIDE_ROOT="$MLEBENCH_AIDE_ROOT"
export RSI_LOOP_ROOT="$PWD"   # run from rsi-loop root
export SRC_OVERLAY=/absolute/path/to/base_v3.overlay
export DST_OVERLAY=/absolute/path/to/new_adapter.overlay
qsub -q long -pe smp 1 -cwd -j y -o overlay_build.log -S /bin/bash environments/aide/build_overlay.sh
```

5. Wait for the build and in-container adapter check to succeed. Export `RSI_OVERLAY_PATH="$DST_OVERLAY"`.
6. Run the task's `environment/check.py`, then follow [operations](../../docs/operations.md) to freeze a new
   state and run the loop. No preparation/check command automatically starts a paid AIDE run.

The legacy `tools/build_overlay_v4.sh` delegates to this builder. Old frozen states must not be resumed after
this protocol reorganization; create a new state. Existing evidence stays unchanged.

## Preflight scope

`python tasks/<task>/environment/check.py --metadata-only` validates local contract references and consistency.
Without that flag it additionally checks runner files, image/overlay paths, public data files, the presence of
the private directory and availability of `qsub` / `apptainer`. It never opens private labels, downloads data,
submits a job or imports libraries inside the container. Missing prerequisites cause a nonzero exit.

This does not certify container dependency compatibility, task schema correctness or isolation. In-container
adapter smoke tests are part of the overlay builder; Python-level hook restrictions are described in
[the boundary report](../../docs/harness_boundaries_20261003.md).
