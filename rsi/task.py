"""Task protocol with explicit environment and evaluation contract references."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

TASKS_DIR = Path(__file__).resolve().parents[1] / "tasks"


@dataclass
class Task:
    name: str
    competition_id: str
    role: str                    # "train": official score goes into memory; "test": never shown to the improver
    metric: dict                 # name, direction, range, implausible_val
    aide: dict                   # mode, models, budget
    train_only_fields: list
    label_column: str
    path: Path
    task_notes: str = ""         # fixed task-side text placed before the harness notes; the improver cannot edit it
    environment: str | None = None  # task-relative manifest path; optional for historical fixtures
    evaluation: str | None = None   # task-relative submission contract path

    @property
    def maximize(self) -> bool:
        return self.metric["direction"] == "maximize"


def load_task(name: str, tasks_dir: Path = TASKS_DIR) -> Task:
    path = Path(tasks_dir) / name
    cfg = json.loads((path / "task.json").read_text())
    if cfg["role"] not in ("train", "test"):
        raise ValueError(f"{name}: role must be train or test, got {cfg['role']!r}")
    return Task(name=cfg["name"], competition_id=cfg["competition_id"], role=cfg["role"], metric=cfg["metric"],
                aide=cfg["aide"], train_only_fields=cfg.get("train_only_fields", []),
                label_column=cfg.get("label_column", ""), path=path, task_notes=cfg.get("task_notes", ""),
                environment=cfg.get("environment"), evaluation=cfg.get("evaluation"))


def list_tasks(tasks_dir: Path = TASKS_DIR) -> list:
    return sorted(p.name for p in Path(tasks_dir).iterdir() if (p / "task.json").is_file())
