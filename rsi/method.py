"""Method freeze: everything that produces, executes, observes and evaluates a harness candidate.

`rsi freeze --model <m> [--overlay <path>]` writes <state>/method.json.  Once it exists, submit, collect, improve and
publish refuse to run when anything below differs.  The harness version files (notes.md, config.json, hooks/) are
NOT part of the method: changing them within the rules is what the loop does.  Changing the method means a new
state directory.

Frozen, by explicit dependency list (path + bytes):
  control/evaluation code  rsi/*.py except __main__ (loop, harness schema, adapter, candidate checks, dynamics,
                           memory, improver prompt/tools, llm call parameters, backend run protocol and information
                           boundary, budget protocol, run reader, task loader, method itself), rsi/verifiers/*.py
  fixed tests              tests/*.py
  task protocol            tasks/*/task.json, instruction.md, prepare.sh (AIDE model config, steps, time, data prep)
  starting harness         harness/H0/** (the template; later versions are variables)
  execution base           the overlay file (path, size, SHA-256) when given; recorded so submit can re-check it
  improver model           name (parameters are in llm.py, frozen above)"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

PKG = Path(__file__).resolve().parent
REPO = PKG.parent


def dependency_files() -> list:
    files = [p for p in PKG.glob("*.py") if p.name != "__main__.py"]
    files += list((PKG / "verifiers").glob("*.py"))
    files += list((REPO / "tests").glob("*.py"))
    for t in (REPO / "tasks").iterdir():
        files += [t / f for f in ("task.json", "instruction.md", "prepare.sh") if (t / f).is_file()]
    files += [p for p in (REPO / "harness" / "H0").rglob("*") if p.is_file() and "__pycache__" not in p.parts]
    return sorted(files)


def _digest(paths, root=REPO) -> str:
    h = hashlib.sha256()
    for p in sorted(paths):
        h.update(p.relative_to(root).as_posix().encode() + b"\0" + p.read_bytes() + b"\0")
    return h.hexdigest()[:12]


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def describe(model: str, overlay: dict | None = None) -> dict:
    from .verifiers import ALL
    deps = dependency_files()
    group = lambda pred: _digest([p for p in deps if pred(p.relative_to(REPO).as_posix())])  # noqa: E731
    return {
        "verifiers": [m.NAME for m in ALL],
        "verifier_digest": group(lambda r: r.startswith("rsi/verifiers/")),
        "improver_digest": group(lambda r: r in ("rsi/improver.py", "rsi/llm.py")),
        "harness_digest": group(lambda r: r in ("rsi/harness.py", "rsi/harness_adapter.py", "rsi/candidate.py")),
        "observation_digest": group(lambda r: r in ("rsi/dynamics.py", "rsi/memory.py", "rsi/run.py")),
        "control_digest": group(lambda r: r.startswith("rsi/") and not r.startswith("rsi/verifiers/")),
        "tests_digest": group(lambda r: r.startswith("tests/")),
        "task_digest": group(lambda r: r.startswith("tasks/")),
        "h0_template_digest": group(lambda r: r.startswith("harness/H0/")),
        "all_files_digest": _digest(deps),
        "files": sorted(p.relative_to(REPO).as_posix() for p in deps),
        "overlay": overlay,
        "improver_model": model,
    }


def overlay_record(path) -> dict | None:
    if not path:
        return None
    p = Path(path).expanduser()
    return {"path": str(p), "size": p.stat().st_size, "sha256": file_sha256(p)}


def digest(desc: dict) -> str:
    return hashlib.sha256(json.dumps(desc, sort_keys=True).encode()).hexdigest()[:12]


class MethodError(RuntimeError):
    pass


def freeze(state: Path, model: str, overlay_path=None) -> dict:
    f = Path(state) / "method.json"
    if f.exists():
        raise MethodError(f"{f} exists; a frozen method is not changed in place (use a new state directory)")
    desc = describe(model, overlay_record(overlay_path))
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({**desc, "digest": digest(desc),
                             "frozen_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}, indent=1) + "\n")
    return desc


def frozen(state: Path) -> dict | None:
    f = Path(state) / "method.json"
    return json.loads(f.read_text()) if f.is_file() else None


def check(state: Path, model: str | None = None, verify_overlay: bool = False) -> str | None:
    """Digest of the frozen method, or None when the state is not frozen.  Raises on any difference.
    With verify_overlay, the overlay file is re-hashed (submit does this; it needs the file to be present)."""
    fz = frozen(state)
    if fz is None:
        return None
    cur = describe(model or fz["improver_model"], fz.get("overlay"))
    keys = [k for k in cur if k != "overlay"]
    diff = [k for k in keys if cur[k] != fz.get(k)]
    if diff:
        changed = sorted(set(cur.get("files", [])) ^ set(fz.get("files", []))) if "files" in diff else []
        raise MethodError(f"method differs from the frozen one in {diff}"
                          + (f" (file list changed: {changed})" if changed else "")
                          + f": frozen {[fz.get(k) for k in diff if k != 'files']}, "
                            f"current {[cur[k] for k in diff if k != 'files']}")
    if verify_overlay and fz.get("overlay"):
        now = overlay_record(fz["overlay"]["path"])
        if now["sha256"] != fz["overlay"]["sha256"]:
            raise MethodError(f"overlay {fz['overlay']['path']} changed since freeze")
    return fz["digest"]
