"""Start an AIDE run with a given harness, through the research repo's own SGE launcher (sge/submit.sh).

Nothing in the research repo is edited; the harness enters only through channels its runner already reads:
a notes file under config/tasks/ selected by PROMPT_VARIANT, and environment variables (harness.aide_env).
The inner agent never gets test feedback (AIDE_FEEDBACK=0, AIDE_TEST_FEEDBACK=0, AIDE_TEST_EXPOSE=none).

OVERLAY_PATH selects a dedicated agent-fix overlay.  Use one that carries the metric-parser fix and the
agent-decision prompt fix (the shared overlay on CRC has both bugs; see the old repo's patches/)."""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

from . import harness as hz
from .task import Task


class BackendError(RuntimeError):
    pass


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
    variant = hz.notes_variant(files["notes.md"])
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

    def submit(self, task: Task, files: dict, dry_run: bool = False) -> dict:
        env = run_env(task, files)
        if self.overlay:
            env["OVERLAY_PATH"] = str(self.overlay)
        plan = {"command": ["bash", "sge/submit.sh", task.competition_id], "env": env}
        if dry_run:
            return {"dry_run": True, **plan}
        for rel in ("sge/submit.sh", "scripts/run_aide.sh"):
            if not (self.root / rel).is_file():
                raise BackendError(f"{self.root} is not a MLE-bench_AIDE checkout (missing {rel})")
        if self.overlay and not self.overlay.is_file():
            raise BackendError(f"overlay not found: {self.overlay}")
        variant = env.get("PROMPT_VARIANT")
        if variant:                       # content-addressed, so an existing file must be identical
            path = self.root / "config" / "tasks" / f"{task.competition_id}.notes.{variant}.txt"
            if path.exists() and path.read_text() != files["notes.md"]:
                raise BackendError(f"{path} exists with different content")
            path.write_text(files["notes.md"])
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


def delivery_problems(run, files: dict) -> list:
    """Did this run actually receive the harness?  An undelivered harness makes H_t and H_t+1 identical."""
    problems = []
    want = hz.notes_variant(files["notes.md"]) or "none"
    got = run.config.get("prompt_variant", "none")
    if got != want:
        problems.append(f"run_config prompt_variant={got!r}, expected {want!r}")
    want_profile = bool(json.loads(files["config.json"]).get("submission_profile"))
    got_profile = run.config.get("sub_stats", "off").lower() in ("1", "on", "true", "yes")
    if want_profile != got_profile:
        problems.append(f"run_config sub_stats={run.config.get('sub_stats', 'off')!r}, harness submission_profile="
                        f"{want_profile}")
    first = next((l for l in files["notes.md"].splitlines() if l.strip()), None)
    if first and (run.notes_delivered is None or first.strip() not in run.notes_delivered):
        problems.append("the first line of notes.md is not in agent/additional_notes.txt")
    return problems
