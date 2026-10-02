"""The loop.  Round t always uses harness H<t>:

    submit   run AIDE on the task with H<t>                        -> rounds/<task>/round_<t>/submit.json
    collect  verifiers on the finished run -> reward vector        -> reward.json, one line in memory.jsonl
    improve  LLM reads memory, edits H<t>                          -> H<t+1> (+ improve.json with the diff)

If the improver makes no change, H<t+1> is a copy of H<t> and the loop goes on.  Test tasks can be submitted
and collected (their reward.json has no official score) but never reach memory or improve.

State layout (any directory, default .state/):
    harness/H0, H1, ...       notes.md + config.json
    rounds/<task>/round_00/   submit.json, reward.json, improve.json
    memory.jsonl
"""
from __future__ import annotations

import difflib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from . import budget
from . import harness as hz
from .backend import delivery_problems
from .memory import Memory
from .run import load_run
from .task import Task
from .verifiers import verify_all

REPO = Path(__file__).resolve().parents[1]


class LoopError(RuntimeError):
    pass


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _dump(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False, default=str) + "\n")


class Loop:
    def __init__(self, state: Path):
        self.state = Path(state)
        self.memory = Memory(self.state / "memory.jsonl")

    def harness_dir(self, t: int) -> Path:
        return self.state / "harness" / f"H{t}"

    def round_dir(self, task: Task, t: int) -> Path:
        return self.state / "rounds" / task.name / f"round_{t:02d}"

    def harness(self, t: int) -> dict:
        d = self.harness_dir(t)
        if not d.is_dir():
            raise LoopError(f"{d} does not exist (run init, or improve round {t - 1} first)")
        return hz.read(d)

    # ------------------------------------------------------------------------------------------- steps
    def init(self) -> Path:
        d = self.harness_dir(0)
        if not d.exists():
            files = hz.read(REPO / "harness" / "H0")
            problems = hz.validate(files)
            if problems:
                raise LoopError(f"repo harness/H0 is invalid: {problems}")
            hz.write(d, files)
        return d

    def submit(self, task: Task, t: int, backend, dry_run=False) -> dict:
        files = self.harness(t)
        if (self.round_dir(task, t) / "submit.json").exists():
            raise LoopError(f"round {t} of {task.name} was already submitted")
        if not dry_run:
            budget.check_submit(self.state)
        out = backend.submit(task, files, dry_run=dry_run)
        out.update(round=t, task=task.name, harness=f"H{t}", harness_digest=hz.digest(files), at=_now())
        if not dry_run:
            _dump(self.round_dir(task, t) / "submit.json", out)
        return out

    def collect(self, task: Task, t: int, run_dir, check_delivery=True) -> dict:
        files = self.harness(t)
        run = load_run(run_dir)
        if not run.nodes:
            raise LoopError(f"{run_dir}: no journal nodes (run not finished?)")
        problems = delivery_problems(run, files) if check_delivery else []
        if problems:
            raise LoopError(f"H{t} did not reach this run: {problems}")
        reward = verify_all(run, task)
        rec = {"round": t, "task": task.name, "role": task.role, "harness": f"H{t}",
               "harness_digest": hz.digest(files), "run_dir": str(run.path), "n_nodes": len(run.nodes),
               "run_cost_usd": budget.run_cost(run.path), "change": self._change_note(t), **reward, "at": _now()}
        _dump(self.round_dir(task, t) / "reward.json", rec)
        if task.role == "train":
            self.memory.append(rec)
        return rec

    def improve(self, task: Task, t: int, improver) -> dict:
        if task.role != "train":
            raise LoopError(f"{task.name} is a test task; it never drives improvement")
        memory = self.memory.records()
        if not any(r["round"] == t and r["task"] == task.name for r in memory):
            raise LoopError(f"round {t} of {task.name} is not in memory yet (collect it first)")
        if self.harness_dir(t + 1).exists():
            raise LoopError(f"H{t + 1} already exists")
        files = self.harness(t)
        task_md = (task.path / "instruction.md").read_text()
        res = improver.improve(files, memory, task_md, task.aide["mode"])
        new = res["files"] if res["outcome"] == "edited" else files
        problems = hz.validate(new)
        if problems:                                   # the improver's own check should have caught this
            res.update(outcome="rejected", reason=problems)
            new = files
        hz.write(self.harness_dir(t + 1), new)
        diff = "".join(difflib.unified_diff(
            [l for f in hz.FILES for l in (f"### {f}\n", *files[f].splitlines(True), "\n")],
            [l for f in hz.FILES for l in (f"### {f}\n", *new[f].splitlines(True), "\n")],
            f"H{t}", f"H{t + 1}"))
        llm = getattr(improver, "llm", None)
        out = {"round": t, "from": f"H{t}", "to": f"H{t + 1}", "outcome": res["outcome"],
               "summary": res.get("summary"), "reason": res.get("reason"), "diff": diff,
               "model": getattr(llm, "model", None), "calls": getattr(llm, "calls", None),
               "cost_usd": getattr(llm, "cost_usd", None), "transcript": res.get("transcript"), "at": _now()}
        _dump(self.round_dir(task, t) / "improve.json", out)
        return out

    def _change_note(self, t: int) -> str:
        """What the improver said when it made H<t> (searched across tasks)."""
        if t == 0:
            return ""
        for f in (self.state / "rounds").glob(f"*/round_{t - 1:02d}/improve.json"):
            imp = json.loads(f.read_text())
            return imp.get("summary") or f"({imp['outcome']}: harness unchanged)"
        return ""
