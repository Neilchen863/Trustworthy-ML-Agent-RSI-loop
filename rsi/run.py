"""Read a finished AIDE run directory (what the research repo's scripts/run_aide.sh leaves behind).

    run_config.txt                key = value lines
    logs/journal.json             {"nodes": [...], "node2parent": {...}}
    code/node_id.txt              id of the submitted node
    submission/submission.csv     the submitted predictions
    grade_report.txt              official MLE-bench grade (after grading)
    agent/additional_notes.txt    the notes AIDE actually received
"""
from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from pathlib import Path


def _term_out(node: dict) -> str:
    out = node.get("_term_out")
    if isinstance(out, list):
        out = "".join(str(x) for x in out)
    out = str(out or "")
    return "" if out == "<OMITTED>" else out


@dataclass
class Node:
    id: str
    step: int
    parent: str | None
    code: str
    plan: str
    term_out: str
    is_buggy: bool
    exc_type: str | None
    val: float | None            # validation metric AIDE recorded (None when buggy or missing)
    analysis: str


@dataclass
class Run:
    path: Path
    config: dict
    nodes: list
    submitted_id: str | None
    grade: dict | None
    notes_delivered: str | None
    submission: list = field(default_factory=list)      # rows of submission.csv as dicts

    @property
    def submitted(self) -> Node | None:
        return next((n for n in self.nodes if n.id == self.submitted_id), None)

    def good_nodes(self) -> list:
        return [n for n in self.nodes if n.val is not None]


def read_config(path: Path) -> dict:
    cfg = {}
    f = path / "run_config.txt"
    if f.is_file():
        for line in f.read_text(errors="ignore").splitlines():
            if "=" in line:
                k, _, v = line.partition("=")
                cfg[k.strip()] = v.strip()
    return cfg


def read_nodes(path: Path) -> list:
    f = path / "logs" / "journal.json"
    if not f.is_file():
        return []
    data = json.loads(f.read_text(errors="ignore"))
    raw = data["nodes"] if isinstance(data, dict) else data
    # Real journals leave each node's own `parent` empty; the tree lives in node2parent.
    parents = data.get("node2parent", {}) if isinstance(data, dict) else {}
    nodes = []
    for n in raw:
        m = n.get("metric") if isinstance(n.get("metric"), dict) else {}
        buggy = bool(n.get("is_buggy"))
        val = m.get("value") if isinstance(m.get("value"), (int, float)) and not buggy else None
        nodes.append(Node(id=n["id"], step=int(n.get("step") or 0), parent=n.get("parent") or parents.get(n["id"]),
                          code=n.get("code") or "", plan=n.get("plan") or "", term_out=_term_out(n),
                          is_buggy=buggy, exc_type=n.get("exc_type"), val=None if val is None else float(val),
                          analysis=n.get("analysis") or ""))
    return sorted(nodes, key=lambda n: n.step)


def read_grade(path: Path) -> dict | None:
    f = path / "grade_report.txt"
    if not f.is_file():
        return None
    text = f.read_text(errors="ignore")
    m = re.search(r'"score":\s*([-\d.eE+]+|null)', text)
    if not m:
        return None
    grade = {"score": None if m.group(1) == "null" else float(m.group(1))}
    for key in ("valid_submission", "submission_exists", "above_median", "any_medal"):
        b = re.search(rf'"{key}":\s*(true|false)', text)
        grade[key] = b.group(1) == "true" if b else None
    for key in ("median_threshold", "bronze_threshold"):
        x = re.search(rf'"{key}":\s*([-\d.eE+]+)', text)
        grade[key] = float(x.group(1)) if x else None
    return grade


def load_run(path) -> Run:
    path = Path(path)
    sid = path / "code" / "node_id.txt"
    notes = path / "agent" / "additional_notes.txt"
    sub = path / "submission" / "submission.csv"
    rows = []
    if sub.is_file():
        with sub.open(newline="") as fh:
            rows = list(csv.DictReader(fh))
    return Run(path=path, config=read_config(path), nodes=read_nodes(path),
               submitted_id=sid.read_text().strip() if sid.is_file() else None, grade=read_grade(path),
               notes_delivered=notes.read_text(errors="ignore") if notes.is_file() else None, submission=rows)
