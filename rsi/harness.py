"""The harness: the only thing the improver can change.

    harness/H<n>/notes.md                text added to every AIDE prompt (task notes channel)
    harness/H<n>/config.json             search settings (schema below)
    harness/H<n>/hooks/on_exec_error.py  diagnose(event) -> str | None, called by the fixed adapter
                                         (rsi/harness_adapter.py) after a node's code raised; the string is
                                         appended to that node's execution output as "[harness note] ..."
    harness/H<n>/manifest.json           written by the controller at publish time, never by the improver

How it reaches AIDE (research repo channels, nothing in that repo is edited):
    notes.md     -> config/tasks/<competition>.notes.<variant>.txt + PROMPT_VARIANT=<variant>
    config.json  -> AIDE_TREE_TOPK / AIDE_TREE_RECENT (agent mode), AIDE_MAX_STAGNATION / AIDE_DEBUG_PROB /
                    AIDE_MAX_DEBUG_DEPTH / num_drafts (rule mode), AIDE_SUB_STATS (submission_profile)
    hooks/       -> the version directory (files + manifest) is staged read-only at
                    <run_dir>/agent/rsi_harness, which the container sees as /home/agent/rsi_harness; the
                    adapter in the dedicated overlay verifies it against the manifest and calls it.

A published version is immutable: written to a temporary directory, then renamed; files are made read-only;
publishing over an existing version is refused.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path

HOOK = "hooks/on_exec_error.py"
FILES = ("notes.md", "config.json", HOOK)          # the improver's whitelist, in digest order
MANIFEST = "manifest.json"
INTERFACE = {"on_exec_error": 1}
MAX_NOTES_CHARS = 8000
MAX_HOOK_CHARS = 20000
HOOK_IMPORTS = {"re", "json", "math", "textwrap", "collections", "itertools", "functools", "string"}
HOOK_FORBIDDEN_NAMES = {"open", "exec", "eval", "compile", "__import__", "globals", "locals", "vars", "getattr",
                        "setattr", "delattr", "breakpoint", "input", "memoryview", "help", "exit", "quit"}

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


def hook_problems(src: str) -> list:
    """Static rules for hooks/on_exec_error.py: stdlib-only imports from a short list, no file/exec/introspection
    builtins, no dunder attribute access, a module-level `def diagnose(event)`."""
    if len(src) > MAX_HOOK_CHARS:
        return [f"{HOOK} has {len(src)} chars; limit {MAX_HOOK_CHARS}"]
    try:
        tree = ast.parse(src)
    except SyntaxError as exc:
        return [f"{HOOK}: syntax error line {exc.lineno}: {exc.msg}"]
    problems = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            bad = [a.name for a in n.names if a.name.split(".")[0] not in HOOK_IMPORTS]
        elif isinstance(n, ast.ImportFrom):
            bad = [n.module or "."] if n.level or (n.module or "").split(".")[0] not in HOOK_IMPORTS else []
        else:
            bad = []
        if bad:
            problems.append(f"{HOOK} line {n.lineno}: import of {bad} not allowed (allowed: {sorted(HOOK_IMPORTS)})")
        if isinstance(n, ast.Name) and n.id in HOOK_FORBIDDEN_NAMES:
            problems.append(f"{HOOK} line {n.lineno}: name {n.id!r} not allowed")
        if isinstance(n, ast.Attribute) and n.attr.startswith("__"):
            problems.append(f"{HOOK} line {n.lineno}: dunder attribute {n.attr!r} not allowed")
    fns = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "diagnose"]
    if not fns:
        problems.append(f"{HOOK}: no module-level `def diagnose(event)`")
    elif len(fns[0].args.args) != 1 or fns[0].args.vararg or fns[0].args.kwarg or fns[0].args.kwonlyargs:
        problems.append(f"{HOOK}: diagnose must take exactly one positional argument (event)")
    return problems


def validate(files: dict, mode: str | None = None, base: dict | None = None) -> list:
    """Problems with a candidate harness ({path: text}); empty list = valid.  With `mode` and `base` (the
    harness it was edited from), keys that the task's mode does not read must stay unchanged: editing them
    changes nothing in the run and only adds noise to the record."""
    problems = [f"file {f!r} is not part of the harness (allowed: {FILES})" for f in files if f not in FILES]
    problems += [f"missing {f}" for f in FILES if f not in files]
    notes = files.get("notes.md", "")
    if len(notes) > MAX_NOTES_CHARS:
        problems.append(f"notes.md has {len(notes)} chars; limit {MAX_NOTES_CHARS}")
    if HOOK in files:
        problems += hook_problems(files[HOOK])
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


def tree_problems(path: Path) -> list:
    """Boundary check of a version directory on disk: only whitelisted regular files (+ manifest), no symlinks,
    nothing that resolves outside the directory."""
    path = Path(path)
    root = path.resolve()
    problems = []
    for p in sorted(path.rglob("*")):
        rel = p.relative_to(path).as_posix()
        if p.is_symlink():
            problems.append(f"{rel}: symlink not allowed")
        elif p.is_dir():
            if rel not in {"hooks"}:
                problems.append(f"{rel}/: directory not allowed")
        elif rel not in FILES and rel != MANIFEST:
            problems.append(f"{rel}: not a harness file")
        elif root not in p.resolve().parents:
            problems.append(f"{rel}: resolves outside the version directory")
    return problems


def read(path: Path) -> dict:
    path = Path(path)
    out = {}
    for f in FILES:
        p = path / f
        if p.is_file() and not p.is_symlink():
            out[f] = p.read_text()
    return out


def write(path: Path, files: dict) -> None:
    """Plain write for scratch copies (candidates, tests).  Published versions go through publish()."""
    for f, text in files.items():
        if f not in FILES:
            raise HarnessError(f"{f!r} is not a harness file")
        p = Path(path) / f
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)


def file_sha256(files: dict) -> dict:
    return {f: hashlib.sha256(files[f].encode()).hexdigest() for f in FILES if f in files}


def digest(files: dict) -> str:
    """Version digest: path and bytes of every whitelisted file, fixed order."""
    h = hashlib.sha256()
    for f in FILES:
        if f in files:
            h.update(f.encode() + b"\0" + files[f].encode() + b"\0")
    return h.hexdigest()[:12]


def manifest(files: dict, parent: str | None, method_digest: str | None, base: dict | None = None) -> dict:
    return {"digest": digest(files), "parent_digest": parent, "interface": INTERFACE, "allowed_paths": list(FILES),
            "files": file_sha256(files), "method_digest": method_digest, **(base or {})}


def publish(path: Path, files: dict, man: dict) -> Path:
    """Write an immutable version: temp dir -> rename; files and dirs read-only.  Refuses to overwrite."""
    path = Path(path)
    if path.exists():
        raise HarnessError(f"{path} exists; a published version is never overwritten")
    if man.get("digest") != digest(files):
        raise HarnessError("manifest digest does not match the files")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=path.name + ".tmp", dir=path.parent))
    try:
        write(tmp, files)
        (tmp / MANIFEST).write_text(json.dumps(man, indent=1, sort_keys=True) + "\n")
        for p in sorted(tmp.rglob("*"), reverse=True):
            p.chmod(0o555 if p.is_dir() else 0o444)
        os.rename(tmp, path)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return path


def read_manifest(path: Path) -> dict | None:
    f = Path(path) / MANIFEST
    return json.loads(f.read_text()) if f.is_file() else None


def stage(src: Path, dst: Path) -> None:
    """Copy a published version (files + manifest) to dst for a run, read-only."""
    src, dst = Path(src), Path(dst)
    for rel in list(FILES) + [MANIFEST]:
        if (src / rel).is_file():
            (dst / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src / rel, dst / rel)
            (dst / rel).chmod(0o444)


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
