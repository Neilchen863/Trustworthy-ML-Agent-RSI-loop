#!/bin/bash
# Compatibility entry point; canonical builder lives with the shared environment.
set -euo pipefail
ROOT="${RSI_LOOP_ROOT:-${SGE_O_WORKDIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}}"
exec bash "$ROOT/environments/aide/build_overlay.sh" "$@"
