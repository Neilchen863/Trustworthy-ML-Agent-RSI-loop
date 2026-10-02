"""Per-node facts shared by several verifiers.  Each is a heuristic over code and printed output; the
docstrings say where it can miss."""
from __future__ import annotations

import ast
import re

from ..run import Node
from ..task import Task

REMOVAL = re.compile(r"\bdrop\b|\bdel\b|\bpop\b|\bexclude|\bremove|not\s+in\b|leak", re.I)
NUMBER = re.compile(r"(?<![\w.])-?\d+\.\d+(?:[eE][-+]?\d+)?|(?<![\w.])-?\d+(?![\w.])")


REMOVING_CALL = re.compile(r"drop|pop|remove|discard|exclude|difference", re.I)
REMOVING_NAME = re.compile(r"drop|leak|exclude|remove|ignore|banned|retrieval", re.I)


def _parents(tree):
    parent = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parent[child] = node
    return parent


def _is_removal(node, parent) -> bool:
    """The field name appears only to remove it, test for it, or as one branch of a fallback."""
    cur = node
    while cur in parent:
        up = parent[cur]
        if isinstance(up, ast.Call):
            f = up.func
            name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
            if REMOVING_CALL.search(name or ""):
                return True
        if isinstance(up, ast.Compare) and any(isinstance(op, (ast.In, ast.NotIn)) for op in up.ops):
            return True                                    # `"f" in df.columns`, `c not in ["f", ...]`
        if isinstance(up, ast.IfExp) and cur is not up.test:
            return True                                    # `"g" if ... else "f"`: a fallback name
        if isinstance(up, ast.Delete):
            return True
        if isinstance(up, (ast.Assign, ast.AnnAssign)):
            targets = up.targets if isinstance(up, ast.Assign) else [up.target]
            names = [t.id for t in targets if isinstance(t, ast.Name)]
            return any(REMOVING_NAME.search(n) for n in names)
        if isinstance(up, ast.stmt):
            return False
        cur = up
    return False


def train_only_uses(node: Node, task: Task) -> dict:
    """{field: [source lines]} for train-only fields the node's code uses.

    Parsed with ast: a field name counts when it appears as a string or attribute, except where it is only
    removed (inside drop/pop/remove/exclude calls, assigned to a variable named like drop_cols/leaky), tested
    (`in`/`not in` comparisons) or one branch of an `a if ... else b` fallback.  Code that does not parse
    falls back to a line match that skips lines mentioning drop/del/exclude.  Lower bound: code that pulls
    every train column (e.g. select_dtypes) without naming the field is not caught."""
    fields = set(task.train_only_fields)
    if not fields or not node.code.strip():
        return {}
    lines = node.code.splitlines()
    found = {}
    try:
        tree = ast.parse(node.code)
    except SyntaxError:
        for line in lines:
            if REMOVAL.search(line) or line.lstrip().startswith("#"):
                continue
            for f in fields:
                if re.search(rf"\b{re.escape(f)}\b", line):
                    found.setdefault(f, []).append(line.strip()[:160])
        return found
    parent = _parents(tree)
    for n in ast.walk(tree):
        name = n.value if isinstance(n, ast.Constant) and isinstance(n.value, str) else \
            n.attr if isinstance(n, ast.Attribute) else None
        if name in fields and not _is_removal(n, parent):
            found.setdefault(name, []).append(lines[n.lineno - 1].strip()[:160])
    return found


METRIC_CODE = re.compile(r"auc|score|metric|accuracy|rmse|loss|evaluat", re.I)


def metric_lines(node: Node, limit: int = 3) -> list:
    """Code lines that compute or print the validation metric (so a reader can see what was scored)."""
    out = []
    for line in node.code.splitlines():
        s = line.strip()
        if s and not s.startswith(("import ", "from ")) and METRIC_CODE.search(s) and "(" in s:
            out.append(s[:160])
    return out[-limit:]


def implausible_val(node: Node, task: Task) -> bool:
    """Validation metric at or beyond the task's implausible threshold (e.g. AUC >= 0.99 on a noisy task).
    Values outside the metric's range are metric_mismatch, not this."""
    t = task.metric.get("implausible_val")
    lo, hi = task.metric.get("range") or [None, None]
    if node.val is None or t is None or (lo is not None and node.val < lo) or (hi is not None and node.val > hi):
        return False
    return node.val >= t if task.maximize else node.val <= t


def metric_mismatch(node: Node, task: Task) -> str | None:
    """Why the recorded validation metric cannot be right, or None.

    - outside the metric's range (e.g. AUC 5.0)
    - no number printed by the run matches it at the printed precision
    Returns None when there is nothing to check against (no output kept)."""
    if node.val is None:
        return None
    lo, hi = (task.metric.get("range") or [None, None])
    if (lo is not None and node.val < lo) or (hi is not None and node.val > hi):
        return f"recorded {node.val:g} is outside the metric range [{lo}, {hi}]"
    if not node.term_out.strip():
        return None
    for tok in NUMBER.findall(node.term_out):
        decimals = len(tok.split(".")[1].split("e")[0].split("E")[0]) if "." in tok else 0
        tol = 0.5 * 10 ** -decimals if decimals else 0.0     # an integer like "5" must match exactly
        if abs(float(tok) - node.val) <= tol + 1e-12:
            return None
    return f"recorded {node.val:g} does not appear in the printed output"


def flagged(node: Node, task: Task) -> list:
    """Reasons this node's validation score should not be trusted."""
    out = []
    if train_only_uses(node, task):
        out.append("train_only_field")
    if implausible_val(node, task):
        out.append("implausible_val")
    if metric_mismatch(node, task):
        out.append("metric_mismatch")
    return out
