"""The loop.  Round t always uses harness version H<t>:

    submit   stage H<t> into a new run directory and start AIDE        -> rounds/<task>/round_<t>/submit.json
    collect  verifiers + search dynamics on the finished run           -> reward.json, dynamics.json, evidence.json,
             + harness receipt (what the adapter actually loaded/did)     harness_receipt.json, one memory line
    improve  LLM reads memory/evidence, proposes a candidate; the fixed -> candidates/<id>/ (always),
             checks run again; pass -> publish H<t+1>, fail -> keep H<t>  H<t+1> (+ improve.json)

If the candidate fails or the improver makes no change, H<t+1> is published as a copy of H<t> (same digest,
manifest says copy_of) and the loop goes on.  Test tasks and replicates never reach memory or improve.

State layout (any directory):
    method.json                frozen method (rsi freeze)
    harness/H0, H1, ...        immutable versions: notes.md, config.json, hooks/on_exec_error.py, manifest.json
    candidates/<id>/           harness/ snapshot, proposal.json, validation.json, diff.patch
    rounds/<task>/round_00/    submit.json, harness_receipt.json, reward.json, dynamics.json, evidence.json,
                               improve.json
    memory.jsonl
"""
from __future__ import annotations

import difflib
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from . import budget, candidate, method
from . import harness as hz
from .backend import delivery_problems, harness_receipt
from .dynamics import extract, memory_view
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


def diff_text(old: dict, new: dict, a: str, b: str) -> str:
    return "".join(difflib.unified_diff(
        [l for f in hz.FILES for l in (f"### {f}\n", *old.get(f, "").splitlines(True), "\n")],
        [l for f in hz.FILES for l in (f"### {f}\n", *new.get(f, "").splitlines(True), "\n")], a, b))


class Loop:
    def __init__(self, state: Path):
        self.state = Path(state)
        self.memory = Memory(self.state / "memory.jsonl")

    def harness_dir(self, t: int) -> Path:
        return self.state / "harness" / f"H{t}"

    def round_dir(self, task: Task, t: int, rep: int = 1) -> Path:
        """Replicates (rep > 1) re-run harness H<t> for evaluation only; they never write memory."""
        return self.state / "rounds" / task.name / (f"round_{t:02d}" + (f"_r{rep}" if rep > 1 else ""))

    @property
    def ledger_path(self) -> Path:
        return self.state / "harness" / "versions.jsonl"

    def ledger(self) -> dict:
        """H<t> -> record written at publish time (digest, file SHA-256s, manifest SHA-256)."""
        if not self.ledger_path.is_file():
            return {}
        return {r["version"]: r for r in map(json.loads, self.ledger_path.read_text().splitlines()) if r}

    def _publish(self, t: int, files: dict, man: dict) -> None:
        d = hz.publish(self.harness_dir(t), files, man)
        rec = {"version": f"H{t}", "digest": man["digest"], "files": man["files"],
               "manifest_sha256": hashlib.sha256((d / hz.MANIFEST).read_bytes()).hexdigest(), "at": _now()}
        with self.ledger_path.open("a") as fh:
            fh.write(json.dumps(rec, sort_keys=True) + "\n")

    def harness(self, t: int) -> dict:
        """Files of H<t>, checked against its manifest and against the publish ledger: a published version must not
        have been modified, and a replaced directory (even with a self-consistent manifest) is detected."""
        d = self.harness_dir(t)
        if not d.is_dir():
            raise LoopError(f"{d} does not exist (run init, or improve round {t - 1} first)")
        files, man = hz.read(d), hz.read_manifest(d)
        if man is None:
            raise LoopError(f"{d} has no manifest.json (not a published version)")
        problems = hz.tree_problems(d)
        if hz.digest(files) != man["digest"] or hz.file_sha256(files) != man["files"] or problems:
            raise LoopError(f"{d} differs from its manifest {problems or ''}")
        rec = self.ledger().get(f"H{t}")
        if rec is None or rec["digest"] != man["digest"] or rec["files"] != man["files"] or \
                rec["manifest_sha256"] != hashlib.sha256((d / hz.MANIFEST).read_bytes()).hexdigest():
            raise LoopError(f"{d} does not match the publish ledger {self.ledger_path} (replaced or not published)")
        return files

    def _base_record(self) -> dict:
        fz = method.frozen(self.state) or {}
        return {"execution_base": {"overlay": fz.get("overlay")}}

    # ------------------------------------------------------------------------------------------- steps
    def init(self) -> Path:
        d = self.harness_dir(0)
        if not d.exists():
            files = hz.read(REPO / "harness" / "H0")
            problems = hz.validate(files)
            if problems:
                raise LoopError(f"repo harness/H0 is invalid: {problems}")
            fz = method.frozen(self.state)
            self._publish(0, files, hz.manifest(files, None, fz and fz["digest"], self._base_record()))
        return d

    def submit(self, task: Task, t: int, backend, dry_run=False, rep: int = 1) -> dict:
        method_digest = method.check(self.state, verify_overlay=not dry_run)
        files = self.harness(t)
        if (self.round_dir(task, t, rep) / "submit.json").exists():
            raise LoopError(f"round {t} of {task.name} was already submitted")
        fz = method.frozen(self.state) or {}
        if fz.get("overlay") and getattr(backend, "overlay", None) and str(backend.overlay) != fz["overlay"]["path"]:
            raise LoopError(f"backend overlay {backend.overlay} is not the frozen one {fz['overlay']['path']}")
        if not dry_run:
            budget.check_submit(self.state)
        out = backend.submit(task, files, self.harness_dir(t), dry_run=dry_run)
        out.update(round=t, replicate=rep, task=task.name, harness=f"H{t}", harness_digest=hz.digest(files),
                   manifest=hz.read_manifest(self.harness_dir(t)), method=method_digest, at=_now())
        if not dry_run:
            _dump(self.round_dir(task, t, rep) / "submit.json", out)
        return out

    def collect(self, task: Task, t: int, run_dir, check_delivery=True, rep: int = 1, reused: str | None = None) -> dict:
        """`reused`: a note saying where an older run (started outside this state, without the adapter) comes from;
        it skips the delivery check and is recorded as provenance."""
        method_digest = method.check(self.state)
        files = self.harness(t)
        man = hz.read_manifest(self.harness_dir(t))
        run = load_run(run_dir)
        if not run.nodes:
            raise LoopError(f"{run_dir}: no journal nodes (run not finished?)")
        rd = self.round_dir(task, t, rep)
        if (rd / "reward.json").exists():
            raise LoopError(f"{rd}/reward.json exists; a collected round is not re-collected in place")
        if reused:
            receipt = {"applicable": False, "reason": "reused run started before/outside this state; no adapter events",
                       "provenance": reused}
            problems = []
        else:
            receipt = harness_receipt(run, man)
            problems = (delivery_problems(run, files, task) + receipt["problems"]) if check_delivery else []
        _dump(rd / "harness_receipt.json", receipt)
        reward = verify_all(run, task)
        dyn, evidence = extract(run, task, reward["results"])
        _dump(rd / "dynamics.json", dyn)
        _dump(rd / "evidence.json", evidence)
        s = dyn["summary"]
        rec = {"round": t, "replicate": rep, "task": task.name, "role": task.role, "harness": f"H{t}",
               "harness_digest": hz.digest(files), "run_dir": str(run.path), "n_nodes": len(run.nodes),
               "n_scored": s["n_scored"], "n_error": s["n_error"], "run_cost_usd": budget.run_cost(run.path),
               "method": method_digest, "change": self._change_note(t), "provenance": reused,
               "delivery": {"ok": not problems, "problems": problems}, **reward,
               "dynamics": memory_view(dyn, evidence),
               "dynamics_source": "reused run (no adapter events)" if reused else "collected", "at": _now()}
        _dump(rd / "reward.json", rec)
        if problems:
            raise LoopError(f"H{t} did not reach this run (recorded in {rd}, not written to memory): {problems}")
        if task.role == "train" and rep == 1:
            self.memory.append(rec)
        return rec

    def evidence(self, task: Task) -> list:
        """Evidence items of every training round in memory (replicates and test tasks are never included)."""
        out = []
        for r in self.memory.records():
            if r["task"] != task.name:
                continue
            f = self.round_dir(task, r["round"]) / "evidence.json"
            if f.is_file():
                out += json.loads(f.read_text())
        return out

    def improve(self, task: Task, t: int, improver) -> dict:
        if task.role != "train":
            raise LoopError(f"{task.name} is a test task; it never drives improvement")
        memory = [r for r in self.memory.records() if r.get("role", "train") == "train"]
        if not any(r["round"] == t and r["task"] == task.name for r in memory):
            raise LoopError(f"round {t} of {task.name} is not in memory yet (collect it first)")
        if self.harness_dir(t + 1).exists():
            raise LoopError(f"H{t + 1} already exists")
        llm = getattr(improver, "llm", None)
        method_digest = method.check(self.state, getattr(llm, "model", None) if llm and llm.model != "scripted" else None)
        files = self.harness(t)
        mode = task.aide["mode"]
        evidence = self.evidence(task)

        def checker(cand, ids):
            return candidate.check_candidate(cand, files, mode, evidence, ids)

        res = improver.improve(files, memory, (task.path / "instruction.md").read_text(), mode, evidence, checker)
        cand_files = res.get("candidate_files") or res["files"]
        cid = f"{task.name}_r{t:02d}_{hz.digest(cand_files)}"
        cdir = self.state / "candidates" / cid
        proposal = res.get("proposal")
        final = None
        outcome = res["outcome"]
        if outcome == "edited":
            method.check(self.state, getattr(llm, "model", None) if llm and llm.model != "scripted" else None)
            final = candidate.check_candidate(cand_files, files, mode, evidence, proposal["evidence_ids"])
            if not final["passed"]:
                outcome = "rejected"
        published = outcome == "edited"
        new = cand_files if published else files
        man = hz.manifest(new, hz.digest(files), method_digest, self._base_record())
        if not published:
            man["copy_of"] = f"H{t}"
        if cand_files != files or outcome != "no_change":
            hz.write(cdir / "harness", cand_files)
            _dump(cdir / "proposal.json", proposal)
            _dump(cdir / "validation.json", {"improver_checks": res.get("checks"), "controller_check": final,
                                             "outcome": outcome})
            (cdir / "diff.patch").write_text(diff_text(files, cand_files, f"H{t}", f"candidate {cid}"))
        self._publish(t + 1, new, man)
        out = {"round": t, "method": method_digest, "from": f"H{t}", "to": f"H{t + 1}", "outcome": outcome,
               "published": published, "candidate": cid if cdir.exists() else None, "proposal": proposal,
               "summary": res.get("summary"), "reason": res.get("reason"),
               "controller_check": final and [[c["check"], c["passed"]] for c in final["checks"]],
               "improver_checks": len(res.get("checks") or []),
               "diff": diff_text(files, new, f"H{t}", f"H{t + 1}"), "candidate_diff": diff_text(files, cand_files, f"H{t}", "candidate"),
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
            return imp.get("summary") if imp.get("published") else f"({imp['outcome']}: harness unchanged)"
        return ""
