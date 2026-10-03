#!/usr/bin/env python3
"""A-E acceptance for one harness step: round t evidence -> candidate -> checks -> H<t+1> -> round t+1 executes it.

    python tools/acceptance_harness.py --state .state-harness --task random_acts_of_pizza --round 0 [--pytest]

  A evidence      memory round t holds verifier results and search dynamics; every evidence id the proposal cites
                  resolves in evidence.json; missing values carry a missing_reason
  B modification  improve outcome edited + published; proposal has evidence ids, observation, hypothesis,
                  changed paths (= actual diff), expected behaviour, regression risks
  C checks        the controller's re-check passed every stage; (--pytest) the fixed test suite passes, including
                  the rollback/failed-candidate tests
  D execution     round t+1 ran H<t+1>: receipt ok (loaded bytes = manifest, adapter = repo adapter); if the hook
                  changed, it was called and appended a note at least once, and the note is in AIDE's prompts
                  (aide.verbose.log) and in the node's journal output
  E evaluation    round t+1 reward vector, dynamics summary, valid nodes, errors, cost, failed calls / fallback
                  (reported, not judged; no score threshold)
Only reads files; prints JSON; exit 0 iff A-D pass (E is a record)."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rsi import harness as hz  # noqa: E402
from rsi.task import load_task  # noqa: E402


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--state", required=True)
    p.add_argument("--task", required=True)
    p.add_argument("--round", type=int, required=True)
    p.add_argument("--pytest", action="store_true", help="also run the fixed test suite for C")
    a = p.parse_args(argv)
    state, t, task = Path(a.state), a.round, load_task(a.task)
    rd = lambda r: state / "rounds" / task.name / f"round_{r:02d}"  # noqa: E731
    mem = [json.loads(l) for l in (state / "memory.jsonl").read_text().splitlines() if l.strip()]
    rec = next(r for r in mem if r["round"] == t and r["task"] == task.name)
    imp = json.loads((rd(t) / "improve.json").read_text())
    evidence = {e["evidence_id"]: e for e in json.loads((rd(t) / "evidence.json").read_text())}
    dyn0 = json.loads((rd(t) / "dynamics.json").read_text())
    old, new = hz.read(state / "harness" / f"H{t}"), hz.read(state / "harness" / f"H{t + 1}")
    report, ok = [], True

    def check(name, passed, facts):
        nonlocal ok
        ok &= bool(passed)
        report.append({"check": name, "pass": bool(passed), "facts": facts})

    prop = imp.get("proposal") or {}
    cited = prop.get("evidence_ids") or []
    unresolved = [i for i in cited if i not in evidence]
    missing_marked = all(("tokens_missing_reason" in n) for n in dyn0["nodes"])
    check("A evidence", rec.get("results") and rec.get("dynamics") and cited and not unresolved and missing_marked,
          {"verifiers": {r["name"]: r["reward"] for r in rec["results"]}, "dynamics_summary": rec["dynamics"]["summary"],
           "provenance": rec.get("provenance"), "cited": cited, "unresolved": unresolved,
           "cited_observations": {i: evidence[i]["observation"] for i in cited if i in evidence},
           "missing_values_marked": missing_marked})

    changed = [f for f in hz.FILES if old.get(f) != new.get(f)]
    fields = ["observation", "hypothesis", "expected_behavior", "regression_risks", "summary"]
    empty = [f for f in fields if not str(prop.get(f) or "").strip()]
    check("B harness modification",
          imp["outcome"] == "edited" and imp.get("published") and changed and not empty
          and sorted(prop.get("changed_paths") or []) == sorted(changed),
          {"outcome": imp["outcome"], "published": imp.get("published"), "changed_files": changed,
           "proposal": prop, "empty_fields": empty, "model": imp.get("model"), "cost_usd": imp.get("cost_usd")})

    cc = imp.get("controller_check") or []
    tests = None
    if a.pytest:
        r = subprocess.run([sys.executable, "-m", "pytest", "-q", "tests"], cwd=ROOT, capture_output=True, text=True)
        tests = {"returncode": r.returncode, "tail": r.stdout.strip().splitlines()[-1:]}
    cand = state / "candidates" / (imp.get("candidate") or "")
    check("C independent checks", cc and all(p for _, p in cc) and (tests is None or tests["returncode"] == 0),
          {"controller_check": cc, "improver_check_calls": imp.get("improver_checks"), "fixed_tests": tests,
           "candidate_dir": str(cand) if cand.is_dir() else None})

    nxt = rd(t + 1) / "reward.json"
    if not nxt.is_file():
        check("D execution", False, {"reason": f"{nxt} missing (round {t + 1} not collected)"})
        print(json.dumps({"ok": False, "report": report}, indent=2, ensure_ascii=False))
        return 1
    r1 = json.loads(nxt.read_text())
    receipt = json.loads((rd(t + 1) / "harness_receipt.json").read_text())
    dyn1 = json.loads((rd(t + 1) / "dynamics.json").read_text())
    run_dir = Path(r1["run_dir"])
    hook_changed = hz.HOOK in changed
    calls = [json.loads(l) for l in (run_dir / "logs" / "harness_events.jsonl").read_text().splitlines()
             if '"event": "call"' in l] if (run_dir / "logs" / "harness_events.jsonl").is_file() else []
    appended = [c for c in calls if c.get("appended")]
    verbose = run_dir / "logs" / "aide.verbose.log"
    vtext = verbose.read_text(errors="ignore") if verbose.is_file() else None
    in_prompts = vtext.count("[harness note]") if vtext is not None else None
    journal = json.loads((run_dir / "logs" / "journal.json").read_text())
    in_journal = sum("[harness note]" in "".join(n.get("_term_out") or []) if isinstance(n.get("_term_out"), list)
                     else "[harness note]" in str(n.get("_term_out") or "") for n in journal["nodes"])
    by_id = {n["node_id"]: n for n in dyn1["nodes"]}
    followups = []
    for c in appended:
        kids = [n for n in dyn1["nodes"] if n["parent_id"] == c["node_id"]]
        followups.append({"node_step": by_id.get(c["node_id"], {}).get("step"), "exc_type": c["exc_type"],
                          "message": (c.get("message") or "")[:160],
                          "children": [{"step": k["step"], "status": k["status"],
                                        "parent_error_cleared": k.get("parent_error_cleared")} for k in kids]})
    d_pass = receipt.get("ok") and r1["harness_digest"] == hz.digest(new) and \
        (not hook_changed or (appended and (in_prompts is None or in_prompts > 0) and in_journal > 0))
    check("D actual execution", d_pass,
          {"receipt_ok": receipt.get("ok"), "receipt_problems": receipt.get("problems"),
           "digest_match": r1["harness_digest"] == hz.digest(new), "hook_changed": hook_changed,
           "hook_calls": len(calls), "hook_calls_appended": len(appended),
           "prompts_with_note": in_prompts, "journal_nodes_with_note": in_journal, "followups": followups,
           "expected_behavior": prop.get("expected_behavior")})

    s1 = dyn1["summary"]
    report.append({"check": "E fixed evaluation (record)", "pass": None, "facts": {
        "vector_t": rec["vector"], "vector_t+1": r1["vector"],
        "scored_nodes": [rec["dynamics"]["summary"]["n_scored"], s1["n_scored"]],
        "errors": [rec["dynamics"]["summary"]["n_error"], s1["n_error"]],
        "error_types_t+1": s1["error_types"], "children_clearing_parent_error_t+1":
            [s1["children_clearing_parent_error"], s1["children_of_error_nodes"]],
        "run_cost_usd_t+1": r1.get("run_cost_usd"), "failed_hook_calls": receipt.get("calls", {}).get("failed"),
        "fallback": receipt.get("fallback")}})
    print(json.dumps({"ok": ok, "report": report}, indent=2, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
