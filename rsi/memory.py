"""Memory: one JSON line per finished round, appended after its verifiers run.

    {"round", "task", "harness", "harness_digest", "run_dir", "vector", "results", "change"}

`change` is what the improver said it changed to make this round's harness (empty for H0).  Test tasks never
write here.  The improver reads the whole file."""
from __future__ import annotations

import json
from pathlib import Path


class Memory:
    def __init__(self, path: Path):
        self.path = Path(path)

    def records(self) -> list:
        if not self.path.is_file():
            return []
        return [json.loads(l) for l in self.path.read_text().splitlines() if l.strip()]

    def append(self, record: dict) -> None:
        if any(r["round"] == record["round"] and r["task"] == record["task"] for r in self.records()):
            raise ValueError(f"memory already has round {record['round']} of {record['task']}")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
