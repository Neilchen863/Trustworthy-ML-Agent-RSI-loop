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


def node(i, code="x = 1\n", val=0.6, buggy=False, out=None, exc=None, analysis=""):
    return {"id": f"n{i}", "step": str(i), "parent": None, "code": code, "plan": "",
            "_term_out": [out if out is not None else f"Validation AUC: {val}\n"], "is_buggy": buggy,
            "exc_type": exc, "metric": {"value": None if buggy else val, "maximize": True}, "analysis": analysis}


def make_run(root: Path, nodes, submitted="n0", preds=(0.1, 0.7, 0.3), score=0.6, variant="none", notes=None):
    root.mkdir(parents=True, exist_ok=True)
    (root / "logs").mkdir(exist_ok=True)
    (root / "logs" / "journal.json").write_text(json.dumps(
        {"nodes": nodes, "node2parent": {n["id"]: nodes[0]["id"] for n in nodes[1:]}}))
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
