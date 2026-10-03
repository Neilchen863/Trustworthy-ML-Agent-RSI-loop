"""Method freeze: the verifiers, the improver prompt/tools/model and the harness schema used by a state.

`rsi freeze --model <m>` writes <state>/method.json.  Once it exists, collect and improve refuse to run
with any other verifier code, improver code, harness schema or improver model, and every reward.json /
improve.json records the method digest.  Changing the method means a new state directory."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

PKG = Path(__file__).resolve().parent


def _digest(paths) -> str:
    h = hashlib.sha256()
    for p in sorted(paths):
        h.update(p.name.encode() + b"\0" + p.read_bytes() + b"\0")
    return h.hexdigest()[:12]


def describe(model: str) -> dict:
    from .verifiers import ALL
    return {
        "verifiers": [m.NAME for m in ALL],
        "verifier_digest": _digest(list((PKG / "verifiers").glob("*.py"))),
        "improver_digest": _digest([PKG / "improver.py"]),
        "harness_digest": _digest([PKG / "harness.py"]),
        "improver_model": model,
    }


def digest(desc: dict) -> str:
    return hashlib.sha256(json.dumps(desc, sort_keys=True).encode()).hexdigest()[:12]


class MethodError(RuntimeError):
    pass


def freeze(state: Path, model: str) -> dict:
    f = Path(state) / "method.json"
    if f.exists():
        raise MethodError(f"{f} exists; a frozen method is not changed in place (use a new state directory)")
    desc = describe(model)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({**desc, "digest": digest(desc),
                             "frozen_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}, indent=1) + "\n")
    return desc


def frozen(state: Path) -> dict | None:
    f = Path(state) / "method.json"
    return json.loads(f.read_text()) if f.is_file() else None


def check(state: Path, model: str | None = None) -> str | None:
    """Digest of the frozen method, or None when the state is not frozen.  Raises on any difference."""
    fz = frozen(state)
    if fz is None:
        return None
    cur = describe(model or fz["improver_model"])
    diff = [k for k in cur if cur[k] != fz.get(k)]
    if diff:
        raise MethodError(f"method differs from the frozen one in {diff}: frozen {[fz.get(k) for k in diff]}, "
                          f"current {[cur[k] for k in diff]}")
    return fz["digest"]
