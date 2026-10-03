"""selection: did the final choice pick the best unflagged node?

unflagged = scored nodes that no verifier flag hits (train-only field, implausible val, metric mismatch,
confirmed preprocessing outside CV).  Unflagged is not the same as trustworthy: the checks are heuristics.
-1 if the submitted node is buggy/missing, or is flagged while an unflagged node exists.
Otherwise reward = -(best unflagged val - submitted val), clipped to [-1, 0] and divided by the metric's range
width when it has one (so on AUC a 0.03 shortfall gives -0.03)."""
from __future__ import annotations

from .base import Result, not_applicable
from .flags import flagged

NAME = "selection"


def verify(run, task) -> Result:
    good = run.good_nodes()
    if not good:
        return not_applicable(NAME, "no node recorded a validation score")
    flags = {n.id: flagged(n, task) for n in good}
    unflagged = [n for n in good if not flags[n.id]]
    sub = run.submitted
    if sub is None or sub.val is None:
        return Result(NAME, -1.0, summary="the submitted node is missing or buggy",
                      evidence=[f"submitted id={run.submitted_id}"])
    sign = 1 if task.maximize else -1
    best = max(unflagged, key=lambda n: sign * n.val) if unflagged else None
    ev = [f"submitted step {sub.step}: val={sub.val:g}, flags={flags.get(sub.id)}",
          f"{len(unflagged)}/{len(good)} scored nodes are unflagged"]
    if best:
        ev.append(f"best unflagged: step {best.step} val={best.val:g}")
    if flags.get(sub.id):
        if best:
            return Result(NAME, -1.0, summary=f"submitted a flagged node ({', '.join(flags[sub.id])}) although "
                                              f"{len(unflagged)} unflagged node(s) existed", evidence=ev)
        return Result(NAME, 0.0, summary="submitted a flagged node, but no unflagged alternative existed", evidence=ev)
    lo, hi = task.metric.get("range") or [None, None]
    width = (hi - lo) if lo is not None and hi is not None else max(abs(best.val), 1e-9)
    gap = max(0.0, sign * (best.val - sub.val)) / width
    return Result(NAME, -min(1.0, gap), value=gap,
                  summary=f"submitted val is {gap:.4f} (range-normalised) below the best unflagged node", evidence=ev)
