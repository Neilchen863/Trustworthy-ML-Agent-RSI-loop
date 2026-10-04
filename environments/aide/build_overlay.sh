#!/bin/bash
# Build the dedicated v4 overlay = v3 overlay (metric-parser + decision-prompt fixes) + the rsi-loop harness adapter.
# Leaves the shared overlay and v3 untouched.  Run on a COMPUTE node (writable overlay mounts on the login node may
# not persist), e.g.
#   qsub -q long -pe smp 1 -cwd -j y -o overlay_v4.log -S /bin/bash environments/aide/build_overlay.sh
#
# Env: AIDE_ROOT (default ~/mlebench-aide), SRC_OVERLAY (default ~/real-rsi-mvp/overlays/v3/agent_fixes_v3.overlay),
#      DST_OVERLAY (default ~/rsi-loop-overlays/v4/agent_fixes_v4.overlay)
set -euo pipefail
AIDE_ROOT="${AIDE_ROOT:-$HOME/mlebench-aide}"
# SGE runs a spooled copy of this script, so take the repo from RSI_LOOP_ROOT / the submit directory.
HERE="${RSI_LOOP_ROOT:-${SGE_O_WORKDIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}}"
[[ -f "$HERE/rsi/harness_adapter.py" ]] || { echo "not the rsi-loop repo: $HERE" >&2; exit 1; }
SRC="${SRC_OVERLAY:-$HOME/real-rsi-mvp/overlays/v3/agent_fixes_v3.overlay}"
DST="${DST_OVERLAY:-$HOME/rsi-loop-overlays/v4/agent_fixes_v4.overlay}"
source "$AIDE_ROOT/scripts/_common.sh"
load_apptainer
[[ -f "$SRC" ]] || { echo "missing $SRC" >&2; exit 1; }
[[ -e "$DST" ]] && { echo "$DST already exists; remove it to rebuild" >&2; exit 1; }
mkdir -p "$(dirname "$DST")"
SRC_MD5_BEFORE=$(md5sum "$SRC" | cut -d' ' -f1)
cp -p "$SRC" "$DST"

echo "== installing rsi/harness_adapter.py into $DST"
"${APPTAINER_BIN}" exec --overlay "$DST" --bind "$HERE/rsi:/mnt/rsi:ro" "${SIF_PATH}" bash -lc 'set -e
  source /opt/conda/etc/profile.d/conda.sh && conda activate agent
  python /mnt/rsi/harness_adapter.py install'

echo "== in-container check (read-only overlay, staged harness, same bind points as run_aide.sh)"
T=$(mktemp -d)
mkdir -p "$T/agent/rsi_harness/hooks" "$T/logs"
cp "$HERE/harness/H0/notes.md" "$HERE/harness/H0/config.json" "$T/agent/rsi_harness/"
cat > "$T/agent/rsi_harness/hooks/on_exec_error.py" <<'PY'
def diagnose(event):
    return "smoke: " + event["exc_type"]
PY
python3 - "$T/agent/rsi_harness" <<'PY'
import hashlib, json, sys, pathlib
d = pathlib.Path(sys.argv[1])
files = {p: hashlib.sha256((d / p).read_bytes()).hexdigest() for p in ("notes.md", "config.json", "hooks/on_exec_error.py")}
(d / "manifest.json").write_text(json.dumps({"digest": "smoke", "interface": {"on_exec_error": 1}, "files": files}))
PY
"${APPTAINER_BIN}" exec --cleanenv --no-home --overlay "$DST:ro" \
    --bind "$T/agent:/home/agent" --bind "$T/logs:/home/logs" "${SIF_PATH}" bash -lc 'set -e
  source /opt/conda/etc/profile.d/conda.sh && conda activate agent
  python - <<PY
import json
import aide.agent as a
from aide import _rsi_harness_adapter as ha
h = a.Agent._rsi_harness
assert h.enabled, h.problems
assert a.Agent.parse_exec_result.__module__ == "aide._rsi_harness_adapter", a.Agent.parse_exec_result.__module__
# push one failed execution through the wrapped method; stop at the original (it needs a full Agent)
class N: id, code = "n1", "x = 1"
class R: exc_type, exc_info = "KeyError", {"args": ["c"]}
r = R(); r.term_out = ["Traceback\n"]
try:
    a.Agent.parse_exec_result(object.__new__(a.Agent), N(), r)
except Exception as exc:
    print("original parse_exec_result raised as expected without a configured Agent:", type(exc).__name__)
assert r.term_out[-1] == "\n[harness note] smoke: KeyError\n", r.term_out
ev = [json.loads(l) for l in open("/home/logs/harness_events.jsonl")]
assert ev[0]["event"] == "load" and ev[0]["match"] and ev[1]["event"] == "call" and ev[1]["appended"], ev
print(json.dumps({"container_check": "ok", "adapter_sha256": ha.adapter_sha(), "events": ev}, indent=1))
PY'
rm -rf "$T"
echo "== source overlay unchanged:"; [[ "$(md5sum "$SRC" | cut -d' ' -f1)" == "$SRC_MD5_BEFORE" ]] && echo yes
echo "== repo adapter sha256: $(sha256sum "$HERE/rsi/harness_adapter.py" | cut -d' ' -f1)"
echo "== built $DST ($(stat -c %s "$DST") bytes) sha256 $(sha256sum "$DST" | cut -d' ' -f1)"
