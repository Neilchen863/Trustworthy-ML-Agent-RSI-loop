#!/bin/bash
# Prepare this task's data on CRC (login node: needs internet and Kaggle credentials).
# Writes ${DATA_DIR}/detecting-insults-in-social-commentary/prepared/{public,private} through the research repo's own script.
set -euo pipefail
: "${MLEBENCH_AIDE_ROOT:?set MLEBENCH_AIDE_ROOT to the MLE-bench_AIDE checkout}"
bash "${MLEBENCH_AIDE_ROOT}/scripts/02_prepare_data.sh" detecting-insults-in-social-commentary
