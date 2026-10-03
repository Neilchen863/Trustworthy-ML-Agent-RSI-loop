"""Start an AIDE run with a given harness, through the research repo's own SGE launcher (sge/submit.sh).

Nothing in the research repo is edited; the harness enters only through channels its runner already reads:
a notes file under config/tasks/ selected by PROMPT_VARIANT, environment variables (harness.aide_env), and the
run directory itself: we choose RUN_ID (run_aide.sge honours a preset RUN_ID), create <run_dir>/agent/rsi_harness
before qsub and copy the published version (files + manifest, read-only) there; run_aide.sh binds <run_dir>/agent
to /home/agent, where the adapter in the dedicated overlay loads it.
The inner agent never gets test feedback (AIDE_FEEDBACK=0, AIDE_TEST_FEEDBACK=0, AIDE_TEST_EXPOSE=none).

OVERLAY_PATH selects the dedicated overlay: v3 fixes (metric parser, decision prompt) + the harness adapter
(tools/build_overlay_v4.sh).  Without the adapter the hook is never called, and collect refuses the run."""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

from . import harness as hz
from . import harness_adapter as ha
from .task import Task


class BackendError(RuntimeError):
    pass


def rendered_notes(task: Task, files: dict) -> str:
    """What AIDE receives: the task's fixed notes (task.json `task_notes`), then the harness notes.  Without task
    notes this is notes.md byte for byte, so prompt variants match runs made before task notes existed."""
    if not task.task_notes.strip():
        return files["notes.md"]
    return task.task_notes.strip() + "\n\n" + files["notes.md"]


def run_env(task: Task, files: dict) -> dict:
    a = task.aide
    env = {
        "AIDE_SELECTION_MODE": a["mode"],
        "AIDE_CODE_MODEL": a["code_model"],
        "AIDE_FEEDBACK_MODEL": a["feedback_model"],
        "TIME_LIMIT_SECS": str(a["run_secs"]),
        "AIDE_STEPS": str(a["steps"]),
        "EXEC_TIMEOUT": str(a["exec_timeout"]),
        "REQ_CPUS": str(a["cpus"]),
        "REQ_GPUS": str(a["gpus"]),
        "AIDE_FEEDBACK": "0",
        "AIDE_TEST_FEEDBACK": "0",
        "AIDE_TEST_EXPOSE": "none",
        "SKIP_TASK_NOTES": "1",          # the harness is the only source of notes
        **hz.aide_env(files, a["mode"]),
    }
    variant = hz.notes_variant(rendered_notes(task, files))
    if variant:
        env["PROMPT_VARIANT"] = variant
    return env


class SgeBackend:
    def __init__(self, aide_root=None, overlay=None):
        root = aide_root or os.environ.get("MLEBENCH_AIDE_ROOT")
        if not root:
            raise BackendError("set MLEBENCH_AIDE_ROOT to the MLE-bench_AIDE checkout on CRC")
        self.root = Path(root)
        overlay = overlay or os.environ.get("RSI_OVERLAY_PATH")
        self.overlay = Path(overlay).expanduser() if overlay else None

    def submit(self, task: Task, files: dict, version_dir=None, dry_run: bool = False) -> dict:
        env = run_env(task, files)
        if self.overlay:
            env["OVERLAY_PATH"] = str(self.overlay)
        variant = env.get("PROMPT_VARIANT") or "stock"
        run_id = f"{time.strftime('%Y%m%d_%H%M%S')}_rsi_{variant}_h{hz.digest(files)}"
        env["RUN_ID"] = run_id
        run_dir = self.root / "runs" / task.competition_id / run_id
        plan = {"command": ["bash", "sge/submit.sh", task.competition_id], "env": env, "run_dir": str(run_dir)}
        if dry_run:
            return {"dry_run": True, **plan}
        for rel in ("sge/submit.sh", "scripts/run_aide.sh"):
            if not (self.root / rel).is_file():
                raise BackendError(f"{self.root} is not a MLE-bench_AIDE checkout (missing {rel})")
        if self.overlay and not self.overlay.is_file():
            raise BackendError(f"overlay not found: {self.overlay}")
        if run_dir.exists():
            raise BackendError(f"{run_dir} exists")
        if env.get("PROMPT_VARIANT"):     # content-addressed, so an existing file must be identical
            path = self.root / "config" / "tasks" / f"{task.competition_id}.notes.{variant}.txt"
            text = rendered_notes(task, files)
            if path.exists() and path.read_text() != text:
                raise BackendError(f"{path} exists with different content")
            path.write_text(text)
        if version_dir is not None:       # immutable code package for this run
            hz.stage(Path(version_dir), run_dir / "agent" / "rsi_harness")
        proc = subprocess.run(plan["command"], cwd=self.root, env={**os.environ, **env},
                              capture_output=True, text=True)
        out = proc.stdout + proc.stderr
        m = re.search(r"[Yy]our job (\d+)", out)
        if proc.returncode != 0 or not m:
            raise BackendError(f"submit.sh failed ({proc.returncode}): {out[-400:]}")
        return {"dry_run": False, "job_id": m.group(1), **plan}

    def find_run_dir(self, task: Task, job_id: str) -> Path | None:
        hits = sorted((self.root / "runs" / task.competition_id).glob(f"*_j{job_id}"))
        return hits[-1] if hits else None


def harness_receipt(run, manifest: dict) -> dict:
    """What the fixed adapter recorded inside the run, compared with the version the loop staged.

    Passes only if there is exactly one load event, its loaded file SHA-256s equal the published manifest, the
    adapter that ran is byte-identical to rsi/harness_adapter.py, and no call fell back silently (failed calls are
    allowed but counted and listed)."""
    events = [ev for _, ev in run.harness_events]
    loads = [ev for ev in events if ev.get("event") == "load"]
    calls = [(i, ev) for i, ev in run.harness_events if ev.get("event") == "call"]
    problems = []
    if len(loads) != 1:
        problems.append(f"expected 1 adapter load event, found {len(loads)} (adapter not installed in the overlay, "
                        f"or harness not staged)")
    load = loads[0] if loads else {}
    if loads and load.get("loaded_files") != manifest["files"]:
        problems.append(f"loaded files {load.get('loaded_files')} != published manifest {manifest['files']}")
    if loads and not load.get("enabled"):
        problems.append(f"adapter disabled the hook: {load.get('problems')}")
    if loads and load.get("adapter_sha256") != ha.adapter_sha():
        problems.append("the adapter in the overlay differs from rsi/harness_adapter.py")
    hook_sha = manifest["files"].get(hz.HOOK)
    wrong = [i for i, ev in calls if ev.get("hook_sha256") != hook_sha]
    if wrong:
        problems.append(f"calls with a different hook SHA-256 at lines {wrong}")
    errors = [n for n in run.nodes if n.exc_type]
    called = {ev.get("node_id") for _, ev in calls}
    missing = [n.step for n in errors if n.id not in called]
    return {"applicable": True, "ok": not problems, "problems": problems,
            "expected": {"digest": manifest["digest"], "files": manifest["files"], "interface": manifest["interface"],
                         "adapter_sha256": ha.adapter_sha()},
            "actual": {"load": load or None, "adapter_sha256": load.get("adapter_sha256"),
                       "loaded_files": load.get("loaded_files"), "harness_dir": load.get("harness_dir")},
            "calls": {"n": len(calls), "ok": sum(ev.get("status") == "ok" for _, ev in calls),
                      "appended": sum(bool(ev.get("appended")) for _, ev in calls),
                      "failed": [{"line": i, "node_id": ev.get("node_id"), "status": ev.get("status"),
                                  "detail": ev.get("detail")} for i, ev in calls if ev.get("status") != "ok"],
                      "error_nodes_without_call": missing},
            "fallback": None if not problems else "hook not (fully) applied; results belong to stock behaviour"}


def delivery_problems(run, files: dict, task: Task) -> list:
    """Did this run actually receive the harness?  An undelivered harness makes H_t and H_t+1 identical."""
    problems = []
    text = rendered_notes(task, files)
    want = hz.notes_variant(text) or "none"
    got = run.config.get("prompt_variant", "none")
    if got != want:
        problems.append(f"run_config prompt_variant={got!r}, expected {want!r}")
    want_profile = bool(json.loads(files["config.json"]).get("submission_profile"))
    got_profile = run.config.get("sub_stats", "off").lower() in ("1", "on", "true", "yes")
    if want_profile != got_profile:
        problems.append(f"run_config sub_stats={run.config.get('sub_stats', 'off')!r}, harness submission_profile="
                        f"{want_profile}")
    for first in (next((l for l in t.splitlines() if l.strip()), None) for t in (task.task_notes, files["notes.md"])):
        if first and (run.notes_delivered is None or first.strip() not in run.notes_delivered):
            problems.append(f"{first.strip()[:60]!r} is not in agent/additional_notes.txt")
    return problems
