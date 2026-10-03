"""Candidate checks: decide whether an edited harness may be published.  Part of the frozen method.

Checks run in this order and all must pass:

  1 boundary      whitelisted paths only, no symlinks / escapes (on disk), valid config, unchanged inactive keys,
                  notes length, hook static rules (stdlib allowlist, no file/exec/introspection, `diagnose(event)`)
  2 interface     the hook imports and runs in the isolated child process (harness_adapter.run_hook) on a probe
                  event and returns None or a string within the length limit
  3 regression    fixed synthetic error events (FIXED_EVENTS) all run without timeout/crash/invalid output, twice,
                  with identical output (deterministic); if the hook changed, it must answer (non-empty message)
                  at least one execution_error event the proposal cites, replayed from evidence.json
  4 smoke         the candidate is staged with a manifest and loaded by harness_adapter.apply() on a stand-in Agent
                  class; one error is pushed through the wrapped parse_exec_result: the load event must match the
                  manifest, the call event must carry the hook's SHA-256, a non-empty message must be appended to
                  term_out with the fixed prefix, and the original parse_exec_result must still be called

The improver may call these checks (tool `check`), but its result is never trusted: the loop runs them again
before publishing.  A failed candidate is kept under candidates/<id>/ and does not change the current version."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from . import harness as hz
from . import harness_adapter as ha

FIXED_EVENTS = [
    ha.make_event("KeyError", {"args": ["missing_col"]},
                  'Traceback (most recent call last):\n  File "runfile.py", line 3, in <module>\n'
                  '    df["missing_col"]\nKeyError: \'missing_col\'\n', 'import pandas as pd\ndf["missing_col"]\n'),
    ha.make_event("ValueError", {"args": ["could not convert string to float: 'N/A'"]},
                  "ValueError: could not convert string to float: 'N/A'\n", "x = float('N/A')\n"),
    ha.make_event("LookupError", {"args": ["\n****\n  Resource punkt not found.\n****\n"]},
                  "LookupError: Resource punkt not found.\n", "import nltk\nnltk.word_tokenize('a b')\n"),
    ha.make_event("TimeoutError", None, "TimeoutError: Execution exceeded the time limit of 10 minutes\n", "while True: pass\n"),
    ha.make_event("MemoryError", {"args": []}, "", ""),
    ha.make_event("", None, "", ""),
    ha.make_event("SyntaxError", {"args": ["invalid syntax", ["runfile.py", 1, 5, "def (", 1, 6]]},
                  "x" * 10000, "y" * 30000),
]


def _check(name, passed, detail=None) -> dict:
    return {"check": name, "passed": bool(passed), "detail": detail}


def cited_events(evidence: list, cited_ids) -> list:
    ids = set(cited_ids or [])
    return [(e["evidence_id"], e["event"]) for e in evidence if e["evidence_id"] in ids and e.get("event")]


def check_candidate(files: dict, base: dict, mode: str, evidence=(), cited_ids=(), workdir=None) -> dict:
    checks = []

    # 1 boundary
    problems = hz.validate(files, mode, base)
    with tempfile.TemporaryDirectory(prefix="rsi_cand_") as tmp:
        d = Path(workdir or tmp) / "harness"
        if not problems:
            hz.write(d, files)
            problems += hz.tree_problems(d)
        checks.append(_check("1 boundary", not problems, problems or None))
        if problems:
            return {"passed": False, "checks": checks}
        src = files[hz.HOOK].encode()

        # 2 interface
        probe = ha.run_hook(src, FIXED_EVENTS[0])
        checks.append(_check("2 interface (isolated import + probe call)", probe["status"] == "ok", probe))
        if probe["status"] != "ok":
            return {"passed": False, "checks": checks}

        # 3 regression
        bad, nondet = [], []
        for i, ev in enumerate(FIXED_EVENTS):
            a, b = ha.run_hook(src, ev), ha.run_hook(src, ev)
            if a["status"] != "ok":
                bad.append({"event": i, **a})
            elif a["message"] != b["message"]:
                nondet.append(i)
        replay = []
        hook_changed = files[hz.HOOK] != base.get(hz.HOOK)
        for eid, ev in cited_events(list(evidence), cited_ids):
            r = ha.run_hook(src, ev)
            replay.append({"evidence_id": eid, "status": r["status"], "message": r["message"]})
            if r["status"] != "ok":
                bad.append({"evidence_id": eid, **r})
        answered = [r for r in replay if r["message"]]
        problems = ([f"fixed/replayed events failed: {bad}"] if bad else []) + \
                   ([f"non-deterministic output on fixed events {nondet}"] if nondet else [])
        if hook_changed and not answered:
            problems.append("the hook changed but returns no message for any cited execution_error evidence "
                            f"(cited with replayable events: {[r['evidence_id'] for r in replay] or 'none'})")
        checks.append(_check("3 regression (fixed events, determinism, cited evidence replay)", not problems,
                             {"problems": problems or None, "replay": replay}))
        if problems:
            return {"passed": False, "checks": checks}

        # 4 smoke through the real adapter
        smoke = smoke_test(files, replay_event=next((ev for eid, ev in cited_events(list(evidence), cited_ids)
                                                     if any(r["evidence_id"] == eid and r["message"] for r in replay)),
                                                    FIXED_EVENTS[0]), tmp=Path(tmp))
        checks.append(_check("4 smoke (adapter.apply on a stand-in Agent)", smoke["passed"], smoke))
    return {"passed": all(c["passed"] for c in checks), "checks": checks}


def smoke_test(files: dict, replay_event: dict, tmp: Path) -> dict:
    stage = tmp / "staged"
    man = hz.manifest(files, parent=None, method_digest=None)
    for rel, text in files.items():
        (stage / rel).parent.mkdir(parents=True, exist_ok=True)
        (stage / rel).write_text(text)
    (stage / hz.MANIFEST).write_text(json.dumps(man))
    log = tmp / "events.jsonl"
    seen = []

    class Node:
        id = "smoke-node"
        code = replay_event.get("code", "")

    class ExecResult:
        term_out = [replay_event.get("traceback_tail", "")]
        exc_type = replay_event.get("exc_type") or "RuntimeError"
        exc_info = {"args": [replay_event.get("exc_message", "")]}

    class Agent:
        def parse_exec_result(self, node, exec_result):
            seen.append("".join(exec_result.term_out))

    h = ha.apply(Agent, harness_dir=str(stage), log_path=str(log))
    res = ExecResult()
    res.term_out = list(ExecResult.term_out)
    Agent().parse_exec_result(Node(), res)
    events = [json.loads(l) for l in log.read_text().splitlines()] if log.is_file() else []
    load = next((e for e in events if e["event"] == "load"), None)
    call = next((e for e in events if e["event"] == "call"), None)
    problems = []
    if not load or not load["match"] or not load["enabled"]:
        problems.append(f"load event missing or not matching: {load}")
    if not call:
        problems.append("no call event")
    else:
        if call.get("hook_sha256") != man["files"][hz.HOOK]:
            problems.append("call event hook_sha256 differs from the manifest")
        if call.get("status") != "ok":
            problems.append(f"hook status {call.get('status')}: {call.get('detail')}")
    if not seen:
        problems.append("original parse_exec_result was not called")
    msg = call.get("message") if call else None
    if msg and not (seen and ha.NOTE_PREFIX + msg in seen[0]):
        problems.append("message not appended to term_out")
    return {"passed": not problems, "problems": problems or None, "message": msg,
            "appended": bool(call and call.get("appended")), "hook_sha256": call.get("hook_sha256") if call else None,
            "enabled": h.enabled}
