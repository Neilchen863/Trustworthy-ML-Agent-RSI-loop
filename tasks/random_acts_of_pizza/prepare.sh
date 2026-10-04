#!/bin/bash
# Compatibility wrapper.
set -euo pipefail
exec bash "$(dirname "${BASH_SOURCE[0]}")/environment/prepare.sh" "$@"
