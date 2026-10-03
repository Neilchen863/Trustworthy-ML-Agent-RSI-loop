"""Search dynamics of one finished run, and the evidence items the improver can cite.

Deterministic extraction from artifacts the run already leaves (journal.json, aide.log, token_usage.jsonl,
cost.txt, harness_events.jsonl).  It describes where nodes failed and where the budget went; it does not compute
a reward (that is the verifiers' job) and it does not judge leakage.

Every non-null observation has a `source` (artifact + JSON pointer or line numbers).  A missing value is null
with a `missing_reason` (not_logged, not_attributed, execution_failed, ...); 0 means an observed zero.

Evidence items get stable ids "<run>:<what>" (e.g. "j1500133:step07:error") and a bounded raw excerpt.  They
are stored in the round's evidence.json; memory keeps only ids and one-line observations, and the improver
reads excerpts by id (read_evidence), never arbitrary files.

confidence: observed      read from a fixed artifact (exception type, parent id, exec time, adapter event)
            suspected     inferred by a fixed rule (same error signature as the parent = suspected propagation)
            self_reported printed or recorded by the candidate code / AIDE's own review (validation values)
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

from .harness_adapter import make_event

SCHEMA_VERSION = 1
EXCERPT_CHARS = 1500
_NUM = re.compile(r"\d+")
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def run_tag(run) -> str:
    m = re.search(r"_j(\d+)$", Path(run.path).name)
    return f"j{m.group(1)}" if m else Path(run.path).name[:40]


def _first_line(exc_info) -> str:
    if isinstance(exc_info, dict) and exc_info.get("args"):
        text = _ANSI.sub("", " ".join(str(a) for a in exc_info["args"]))
        lines = [l.strip() for l in text.splitlines() if l.strip() and not set(l.strip()) <= set("*")]
        return lines[0] if lines else ""
    return ""


def _code_frame(node):
    """Last frame of the node's own code (runfile.py) in exc_stack: (func, source line) or None."""
    for fr in reversed(node.exc_stack or []):
        if isinstance(fr, (list, tuple)) and len(fr) >= 4 and str(fr[0]).endswith("runfile.py"):
            return str(fr[2]), str(fr[3]).strip()
    return None


def _code_frame_from_text(term_out: str):
    hits = re.findall(r'File "runfile\.py", line (\d+), in (\S+)\n\s*(.+)', term_out)
    return (hits[-1][1], hits[-1][2].strip()) if hits else None


def signature(node) -> str | None:
    """exception type + normalised first message line + innermost line of the node's own code."""
    if not node.exc_type:
        return None
    msg = _NUM.sub("N", _first_line(node.exc_info))[:80]
    fr = _code_frame(node) or _code_frame_from_text(node.term_out)
    return f"{node.exc_type}: {msg}" + (f" @ {fr[1][:80]}" if fr else "")


def _excerpt(text: str) -> str:
    text = text or ""
    return text if len(text) <= EXCERPT_CHARS else "..." + text[-EXCERPT_CHARS:]


def _grep(path: Path, pattern: str) -> list:
    if not path.is_file():
        return []
    rx = re.compile(pattern)
    return [(i, l.rstrip()) for i, l in enumerate(path.read_text(errors="ignore").splitlines(), 1) if rx.search(l)]


def extract(run, task, verifier_results=None) -> tuple:
    """Returns (dynamics dict, evidence list)."""
    tag = run_tag(run)
    by_id = {n.id: n for n in run.nodes}
    sigs = {n.id: signature(n) for n in run.nodes}
    events_by_node = {}
    for line_no, ev in run.harness_events:
        if ev.get("event") == "call":
            events_by_node.setdefault(ev.get("node_id"), []).append((line_no, ev))

    def ancestors(n):
        seen = set()
        while n.parent and n.parent in by_id and n.parent not in seen:
            seen.add(n.parent)
            n = by_id[n.parent]
            yield n

    nodes, evidence = [], []
    for n in run.nodes:
        ptr = f"/nodes/{n.index}"
        parent = by_id.get(n.parent)
        status = "scored" if n.val is not None else "error" if n.exc_type else "buggy_no_exception" if n.is_buggy \
            else "unknown"
        rec = {"node_id": n.id, "parent_id": n.parent, "step": n.step, "status": status,
               "source": {"artifact": "logs/journal.json", "pointer": ptr}}
        rec["metric"] = ({"name": task.metric.get("name"), "maximize": n.maximize, "value": n.val,
                          "confidence": "self_reported", "source": {"artifact": "logs/journal.json",
                                                                    "pointer": ptr + "/metric"}}
                         if n.val is not None else None)
        if n.val is None:
            rec["metric_missing_reason"] = "execution_failed" if n.exc_type else "not_recorded"
        rec["exec_time_s"] = n.exec_time
        if n.exec_time is None:
            rec["exec_time_missing_reason"] = "not_logged"
        rec["tokens"] = None
        rec["tokens_missing_reason"] = "not_attributed"   # token_usage.jsonl has no node ids
        if n.exc_type:
            sig = sigs[n.id]
            anc = list(ancestors(n))
            first = not any(sigs.get(a.id) == sig for a in anc)
            same_as_parent = parent is not None and sigs.get(parent.id) == sig
            eid = f"{tag}:step{n.step:02d}:error"
            rec["error"] = {"exc_type": n.exc_type, "message": _first_line(n.exc_info)[:300], "signature": sig,
                            "code_frame": list(_code_frame(n) or _code_frame_from_text(n.term_out) or []) or None,
                            "first_in_lineage": first, "suspected_propagation_from": parent.id if same_as_parent
                            else None, "evidence_id": eid}
            evidence.append({"evidence_id": eid, "kind": "execution_error", "node_id": n.id, "step": n.step,
                             "source": {"artifact": "logs/journal.json", "pointer": ptr + "/_term_out"},
                             "observation": f"step {n.step} (parent step {parent.step if parent else None}): {sig}",
                             "confidence": "observed", "excerpt": _excerpt(n.term_out) or _excerpt(
                                 json.dumps(n.exc_info)[:EXCERPT_CHARS]),
                             # the exact input the on_exec_error hook would get for this node (replayed by checks)
                             "event": make_event(n.exc_type, n.exc_info, n.term_out, n.code)})
            if same_as_parent:
                evidence.append({"evidence_id": f"{tag}:step{n.step:02d}:propagation", "kind": "propagation",
                                 "node_id": n.id, "step": n.step,
                                 "source": {"artifact": "logs/journal.json", "pointer": ptr},
                                 "observation": f"step {n.step} repeats the error signature of its parent step "
                                                f"{parent.step} (suspected propagation, not independent)",
                                 "confidence": "suspected", "excerpt": sig})
        calls = events_by_node.get(n.id, [])
        if calls:
            rec["harness_calls"] = []
            for k, (line_no, ev) in enumerate(calls, 1):
                eid = f"{tag}:step{n.step:02d}:hook{k}"
                rec["harness_calls"].append({"evidence_id": eid, "status": ev.get("status"),
                                             "appended": ev.get("appended"), "line": line_no})
                evidence.append({"evidence_id": eid, "kind": "harness_call", "node_id": n.id, "step": n.step,
                                 "source": {"artifact": "logs/harness_events.jsonl", "lines": [line_no]},
                                 "observation": f"hook on_exec_error for step {n.step} ({ev.get('exc_type')}): "
                                                f"status={ev.get('status')}, appended={ev.get('appended')}",
                                 "confidence": "observed", "excerpt": _excerpt(ev.get("message") or "(no message)")})
        nodes.append(rec)

    # children of error nodes: did the next attempt clear the parent's error signature?
    for rec in nodes:
        n = by_id[rec["node_id"]]
        parent = by_id.get(n.parent)
        if parent is not None and parent.exc_type:
            rec["parent_error_cleared"] = sigs.get(n.id) != sigs.get(parent.id)

    # submission and selection
    sub = run.submitted
    aide_log = Path(run.path) / "logs" / "aide.log"
    choice = _grep(aide_log, r"\[agent submit\] LLM chose node=")
    submission = {"node_id": run.submitted_id, "step": sub.step if sub else None, "val": sub.val if sub else None,
                  "source": {"artifact": "code/node_id.txt"},
                  "selection_record": {"artifact": "logs/aide.log", "lines": [choice[-1][0]],
                                       "text": choice[-1][1][-200:]} if choice else None}
    if not choice:
        submission["selection_record_missing_reason"] = "not_logged"
    evidence.append({"evidence_id": f"{tag}:submission", "kind": "submission", "node_id": run.submitted_id,
                     "step": submission["step"], "source": submission["selection_record"] or submission["source"],
                     "observation": f"submitted step {submission['step']} val={submission['val']}",
                     "confidence": "observed", "excerpt": (choice[-1][1][-EXCERPT_CHARS:] if choice else "")})

    # verifier findings get ids too, so the improver cites them the same way
    for r in verifier_results or []:
        if not r.get("applicable") or r["name"] == "official_score":
            continue
        for k, e in enumerate(r.get("evidence") or [], 1):
            evidence.append({"evidence_id": f"{tag}:verifier:{r['name']}:{k}", "kind": "verifier_finding",
                             "source": {"artifact": "reward.json", "verifier": r["name"]},
                             "observation": f"{r['name']} (reward {r['reward']}): {r['summary']}"[:300],
                             "confidence": "observed", "excerpt": str(e)[:EXCERPT_CHARS]})

    # run-level budget
    tok = Path(run.path) / "logs" / "token_usage.jsonl"
    tokens = None
    if tok.is_file():
        rows = [json.loads(l) for l in tok.read_text(errors="ignore").splitlines() if l.strip().startswith("{")]
        tokens = {"calls": len(rows), "in": sum(r.get("in", 0) for r in rows), "out": sum(r.get("out", 0) for r in rows),
                  "source": {"artifact": "logs/token_usage.jsonl"}}
    from .budget import run_cost
    cost = run_cost(run.path)

    good = [n for n in run.nodes if n.val is not None]
    errors = [r for r in nodes if r.get("error")]
    first = [r for r in errors if r["error"]["first_in_lineage"]]
    flagged = {}
    if good:
        from .verifiers.flags import flagged as _flagged
        flagged = {n.id: _flagged(n, task) for n in good}
    sign = 1 if task.maximize else -1
    best_raw = max(good, key=lambda n: sign * n.val) if good else None
    unfl = [n for n in good if not flagged.get(n.id)]
    best_unfl = max(unfl, key=lambda n: sign * n.val) if unfl else None
    hook_events = [ev for _, ev in run.harness_events]
    loads = [ev for ev in hook_events if ev.get("event") == "load"]
    calls = [ev for ev in hook_events if ev.get("event") == "call"]
    summary = {
        "n_nodes": len(run.nodes), "n_scored": len(good), "n_error": len(errors),
        "n_buggy_no_exception": sum(r["status"] == "buggy_no_exception" for r in nodes),
        "n_unknown": sum(r["status"] == "unknown" for r in nodes),
        "error_types": dict(Counter(r["error"]["exc_type"] for r in errors).most_common()),
        "error_signatures_first_in_lineage": dict(Counter(r["error"]["signature"] for r in first).most_common()),
        "n_errors_first_in_lineage": len(first),
        "n_errors_suspected_propagation": sum(bool(r["error"]["suspected_propagation_from"]) for r in errors),
        "timing_coverage": round(sum(n.exec_time is not None for n in run.nodes) / len(run.nodes), 3) if run.nodes else None,
        "exec_time_total_s": round(sum(n.exec_time or 0 for n in run.nodes), 1),
        "token_coverage_per_node": 0.0, "tokens_run": tokens,
        "cost_usd_run": cost, "cost_missing_reason": None if cost is not None else "no cost.txt",
        "best_raw_val": {"step": best_raw.step, "val": best_raw.val} if best_raw else None,
        "best_unflagged_val": {"step": best_unfl.step, "val": best_unfl.val} if best_unfl else None,
        "n_flagged_scored": sum(bool(v) for v in flagged.values()),
        "children_of_error_nodes": sum("parent_error_cleared" in r for r in nodes),
        "children_clearing_parent_error": sum(bool(r.get("parent_error_cleared")) for r in nodes),
        "harness_loads": len(loads), "harness_calls": len(calls),
        "harness_calls_appended": sum(bool(ev.get("appended")) for ev in calls),
        "harness_calls_failed": sum(ev.get("status") not in ("ok",) for ev in calls),
    }
    model_training = {"curves": None, "missing_reason": "not_logged (no fixed collector for per-epoch metrics; "
                                                        "values printed by node code would be self_reported)"}
    dyn = {"schema_version": SCHEMA_VERSION, "run": tag, "run_dir": str(run.path), "summary": summary,
           "submission": submission, "nodes": nodes, "model_training": model_training}
    return dyn, evidence


def memory_view(dyn: dict, evidence: list, limit: int = 40) -> dict:
    """What goes into memory: the summary and an index of evidence ids with one-line observations."""
    order = {"execution_error": 0, "harness_call": 1, "verifier_finding": 2, "propagation": 3, "submission": 4}
    idx = sorted(evidence, key=lambda e: (order.get(e["kind"], 9), e.get("step") or 0))
    return {"schema_version": dyn["schema_version"], "summary": dyn["summary"],
            "evidence_index": [{"evidence_id": e["evidence_id"], "kind": e["kind"], "observation": e["observation"],
                                "confidence": e["confidence"]} for e in idx[:limit]],
            "evidence_omitted": max(0, len(idx) - limit)}
