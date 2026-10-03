#!/usr/bin/env python3
"""Acceptance check for one closed loop step: round t evidence -> improve -> H<t+1> -> round t+1.

    python tools/acceptance.py --state .state-accept --task random_acts_of_pizza --round 0

Checks, each reported pass/fail with the facts behind it:
  A evidence   memory round t has concrete evidence (code lines / counts) for the verifiers that fired
  B edit       H<t+1> differs from H<t>; changed config keys are read in this mode; notes do not rely on the
               loop's own vocabulary; the improver summary or notes quote something from the evidence
  C delivery   round t+1 ran with H<t+1> (collect's delivery check passed and recorded the same harness digest),
               and the notes text appears in the LLM prompts AIDE actually sent (aide.verbose.log, if present)
  D compliance for each verifier that fired at round t: its value at round t+1 (n = 1, a record, not an effect)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rsi import harness as hz  # noqa: E402
from rsi.task import load_task  # noqa: E402

LOOP_WORDS = re.compile(r"\b(un)?flagged\b|\bverifier|\breward\b|\btrusted\b|\bimplausible_validation\b", re.I)
TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_.]{5,}")


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--state", required=True)
    p.add_argument("--task", required=True)
    p.add_argument("--round", type=int, required=True)
    a = p.parse_args(argv)
    state, t, task = Path(a.state), a.round, load_task(a.task)
    rd = lambda r: state / "rounds" / task.name / f"round_{r:02d}"
    mem = [json.loads(l) for l in (state / "memory.jsonl").read_text().splitlines() if l.strip()]
    rec = next(r for r in mem if r["round"] == t and r["task"] == task.name)
    imp = json.loads((rd(t) / "improve.json").read_text())
    old, new = hz.read(state / "harness" / f"H{t}"), hz.read(state / "harness" / f"H{t + 1}")
    report, ok = [], True

    def check(name, passed, facts):
        nonlocal ok
        ok &= bool(passed)
        report.append({"check": name, "pass": bool(passed), "facts": facts})

    fired = [r for r in rec["results"] if r["applicable"] and r["name"] != "official_score" and (r["reward"] or 0) < 0]
    concrete = [r["name"] for r in fired if r["evidence"]]
    check("A evidence in memory", fired and len(concrete) == len(fired),
          {"fired": [f"{r['name']}={r['reward']:.3f}" for r in fired], "with_evidence": concrete,
           "method": rec.get("method")})

    cfg_old, cfg_new = json.loads(old["config.json"]), json.loads(new["config.json"])
    changed_keys = sorted(k for k in cfg_new if cfg_new[k] != cfg_old.get(k))
    inactive = [k for k in changed_keys if k not in hz.active_keys(task.aide["mode"])]
    notes_changed = old["notes.md"] != new["notes.md"]
    loop_words = sorted({m.group(0) for m in LOOP_WORDS.finditer(new["notes.md"])})
    ev_tokens = {tok for r in fired for e in r["evidence"] for tok in TOKEN.findall(e)}
    quoted = sorted(tok for tok in ev_tokens if tok in (imp.get("summary") or "") + new["notes.md"])
    check("B traceable, effective edit",
          imp["outcome"] == "edited" and (notes_changed or changed_keys) and not inactive and not loop_words and quoted,
          {"outcome": imp["outcome"], "model": imp.get("model"), "method": imp.get("method"),
           "config_changed": changed_keys, "inactive_keys_changed": inactive, "notes_changed": notes_changed,
           "loop_vocabulary_in_notes": loop_words, "evidence_tokens_quoted": quoted[:12],
           "summary": imp.get("summary")})

    nxt = rd(t + 1) / "reward.json"
    if not nxt.is_file():
        check("C delivered to round t+1", False, {"reason": f"{nxt} missing (round {t + 1} not collected)"})
        print(json.dumps({"ok": False, "report": report}, indent=2, ensure_ascii=False))
        return 1
    r1 = json.loads(nxt.read_text())
    verbose = Path(r1["run_dir"]) / "logs" / "aide.verbose.log"
    notes_lines = [l.strip() for l in new["notes.md"].splitlines() if len(l.strip()) > 30]
    probe = notes_lines[0][:80] if notes_lines else None
    prompt_hits = verbose.read_text(errors="ignore").count(probe) if (probe and verbose.is_file()) else None
    check("C delivered to round t+1",
          r1["harness"] == f"H{t + 1}" and r1["harness_digest"] == hz.digest(new) and (prompt_hits is None or prompt_hits > 0),
          {"run_dir": r1["run_dir"], "harness": r1["harness"], "digest_match": r1["harness_digest"] == hz.digest(new),
           "notes_probe": probe, "prompts_containing_notes": prompt_hits,
           "delivery_check": "passed at collect (collect refuses undelivered runs)"})

    comp = []
    for r in fired:
        after = next((x for x in r1["results"] if x["name"] == r["name"]), None)
        comp.append({"verifier": r["name"], "round_t": r["reward"], "round_t+1": after and after["reward"],
                     "followed": None if after is None or after["reward"] is None else after["reward"] == 0,
                     "after_summary": after and after["summary"]})
    report.append({"check": "D compliance (recorded, n = 1)", "pass": None, "facts": comp,
                   "official_score": {"round_t": rec["vector"].get("official_score"),
                                      "round_t+1": r1["vector"].get("official_score")}})
    print(json.dumps({"ok": ok, "report": report}, indent=2, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
