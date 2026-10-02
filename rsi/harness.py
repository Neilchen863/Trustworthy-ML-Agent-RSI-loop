"""The harness: the only thing the improver can change.

    harness/H<n>/notes.md      text added to every AIDE prompt (task notes channel)
    harness/H<n>/config.json   search settings (schema below)

How it reaches AIDE (research repo channels, nothing in that repo is edited):
    notes.md     -> config/tasks/<competition>.notes.<variant>.txt + PROMPT_VARIANT=<variant>
    config.json  -> AIDE_TREE_TOPK / AIDE_TREE_RECENT (agent mode), AIDE_MAX_STAGNATION / AIDE_DEBUG_PROB /
                    AIDE_MAX_DEBUG_DEPTH / num_drafts (rule mode), AIDE_SUB_STATS (submission_profile)
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

FILES = ("notes.md", "config.json")
MAX_NOTES_CHARS = 8000

# key -> (type, min, max, which mode reads it)
CONFIG_SCHEMA = {
    "tree_topk": (int, 1, 20, "agent"),
    "tree_recent": (int, 1, 20, "agent"),
    "max_stagnation": (int, 3, 100, "rule"),
    "debug_prob": (float, 0.0, 1.0, "rule"),
    "max_debug_depth": (int, 1, 50, "rule"),
    "num_drafts": (int, 1, 20, "rule"),
    "submission_profile": (bool, None, None, "both"),
}


class HarnessError(ValueError):
    pass


def active_keys(mode: str) -> list:
    return [k for k, (_, _, _, m) in CONFIG_SCHEMA.items() if m in (mode, "both")]


def validate(files: dict, mode: str | None = None, base: dict | None = None) -> list:
    """Problems with a candidate harness ({filename: text}); empty list = valid.  With `mode` and `base` (the
    harness it was edited from), keys that the task's mode does not read must stay unchanged: editing them
    changes nothing in the run and only adds noise to the record."""
    problems = [f"file {f!r} is not part of the harness (allowed: {FILES})" for f in files if f not in FILES]
    problems += [f"missing {f}" for f in FILES if f not in files]
    notes = files.get("notes.md", "")
    if len(notes) > MAX_NOTES_CHARS:
        problems.append(f"notes.md has {len(notes)} chars; limit {MAX_NOTES_CHARS}")
    try:
        cfg = json.loads(files.get("config.json", "{}"))
    except json.JSONDecodeError as exc:
        return problems + [f"config.json is not valid JSON: {exc}"]
    if not isinstance(cfg, dict):
        return problems + ["config.json must be an object"]
    for key, val in cfg.items():
        if key not in CONFIG_SCHEMA:
            problems.append(f"config.json: unknown key {key!r} (allowed: {sorted(CONFIG_SCHEMA)})")
            continue
        typ, lo, hi, _ = CONFIG_SCHEMA[key]
        ok = isinstance(val, bool) if typ is bool else isinstance(val, (int, float)) and not isinstance(val, bool)
        if typ is int and ok and float(val) != int(val):
            ok = False
        if not ok:
            problems.append(f"config.json: {key} must be {typ.__name__}")
        elif lo is not None and not lo <= val <= hi:
            problems.append(f"config.json: {key}={val} outside [{lo}, {hi}]")
    missing = [k for k in CONFIG_SCHEMA if k not in cfg]
    if missing:
        problems.append(f"config.json: missing keys {missing}")
    if mode and base:
        old = json.loads(base["config.json"])
        for k in CONFIG_SCHEMA:
            if k not in active_keys(mode) and cfg.get(k) != old.get(k):
                problems.append(f"config.json: {k} is read only in {CONFIG_SCHEMA[k][3]} mode and this task runs in "
                                f"{mode} mode; leave it at {old.get(k)!r}")
    return problems


def read(path: Path) -> dict:
    return {f: (Path(path) / f).read_text() for f in FILES if (Path(path) / f).is_file()}


def write(path: Path, files: dict) -> None:
    Path(path).mkdir(parents=True, exist_ok=True)
    for f, text in files.items():
        (Path(path) / f).write_text(text)


def digest(files: dict) -> str:
    h = hashlib.sha256()
    for f in FILES:
        h.update(f.encode() + b"\0" + files.get(f, "").encode() + b"\0")
    return h.hexdigest()[:12]


def aide_env(files: dict, mode: str) -> dict:
    """Environment variables that carry config.json into the research repo's runner."""
    cfg = json.loads(files["config.json"])
    env = {}
    if mode == "agent":
        env["AIDE_TREE_TOPK"] = str(cfg["tree_topk"])
        env["AIDE_TREE_RECENT"] = str(cfg["tree_recent"])
    else:
        env["AIDE_MAX_STAGNATION"] = str(cfg["max_stagnation"])
        env["AIDE_DEBUG_PROB"] = str(cfg["debug_prob"])
        env["AIDE_MAX_DEBUG_DEPTH"] = str(cfg["max_debug_depth"])
        env["AIDE_EXTRA_KWARGS"] = f"agent.search.num_drafts={cfg['num_drafts']}"
    if cfg["submission_profile"]:
        env["AIDE_SUB_STATS"] = "1"
    return env


def notes_variant(notes: str) -> str | None:
    """Content-addressed PROMPT_VARIANT name; None for empty notes (stock AIDE)."""
    return "rsi_" + hashlib.sha256(notes.encode()).hexdigest()[:10] if notes.strip() else None
