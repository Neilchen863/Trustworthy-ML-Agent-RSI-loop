"""Build a fake run directory in the same shape run_aide.sh writes (parents only in node2parent)."""
from __future__ import annotations

import json
from pathlib import Path

GRADE = '''[cli.py:203] {{
    "competition_id": "random-acts-of-pizza",
    "score": {score},
    "bronze_threshold": 0.6921,
    "median_threshold": 0.599595,
    "above_median": false,
    "submission_exists": true,
    "valid_submission": true
}}'''


def node(i, code="x = 1\n", val=0.6, buggy=False, out=None, exc=None, analysis="", exc_info=None, exec_time=1.0):
    return {"id": f"n{i}", "step": str(i), "parent": None, "code": code, "plan": "",
            "_term_out": [out if out is not None else f"Validation AUC: {val}\n"], "is_buggy": buggy or bool(exc),
            "exc_type": exc, "exc_info": exc_info, "exec_time": exec_time,
            "metric": {"value": None if (buggy or exc) else val, "maximize": True}, "analysis": analysis}


def make_run(root: Path, nodes, submitted="n0", preds=(0.1, 0.7, 0.3), score=0.6, variant="none", notes=None,
             parents=None):
    """parents: {child id: parent id}; default every node's parent is the first node."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "logs").mkdir(exist_ok=True)
    (root / "logs" / "journal.json").write_text(json.dumps(
        {"nodes": nodes, "node2parent": parents if parents is not None else {n["id"]: nodes[0]["id"] for n in nodes[1:]}}))
    (root / "code").mkdir(exist_ok=True)
    (root / "code" / "node_id.txt").write_text(submitted)
    (root / "submission").mkdir(exist_ok=True)
    rows = "\n".join(f"t3_{i},{p}" for i, p in enumerate(preds))
    (root / "submission" / "submission.csv").write_text("request_id,requester_received_pizza\n" + rows + "\n")
    if score is not None:
        (root / "grade_report.txt").write_text(GRADE.format(score=score))
    (root / "run_config.txt").write_text(f"competition      = random-acts-of-pizza\nprompt_variant   = {variant}\n")
    if notes is not None:
        (root / "agent").mkdir(exist_ok=True)
        (root / "agent" / "additional_notes.txt").write_text(notes)
    return root


def run_adapter(root: Path, version_dir: Path, nodes: list) -> None:
    """What the dedicated overlay does inside a real run: stage the version at <run>/agent/rsi_harness, apply the
    fixed adapter to an Agent class, push every node with an exception through parse_exec_result.  Appended notes
    end up in the node's _term_out, as in a real journal."""
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from rsi import harness as hz
    from rsi import harness_adapter as ha
    staged = root / "agent" / "rsi_harness"
    hz.stage(version_dir, staged)
    (root / "logs").mkdir(parents=True, exist_ok=True)

    class Agent:
        def parse_exec_result(self, node, exec_result):
            node.term_out = "".join(exec_result.term_out)

    class N:
        pass

    class R:
        pass

    ha.apply(Agent, harness_dir=str(staged), log_path=str(root / "logs" / "harness_events.jsonl"))
    for n in nodes:
        if not n.get("exc_type"):
            continue
        nd, res = N(), R()
        nd.id, nd.code = n["id"], n["code"]
        res.term_out, res.exc_type, res.exc_info = list(n["_term_out"]), n["exc_type"], n.get("exc_info")
        Agent().parse_exec_result(nd, res)
        n["_term_out"] = [nd.term_out]


def make_harness_run(root: Path, nodes, version_dir: Path, **kw):
    """make_run for a run that went through the adapter with the given published version."""
    run_adapter(root, version_dir, nodes)
    return make_run(root, nodes, **kw)
