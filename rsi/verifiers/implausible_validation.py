"""implausible_validation: validation scores too good to be true.

value  = fraction of nodes with a recorded validation score at/beyond task.json `metric.implausible_val`
reward = -value, or -1 if the submitted node is one of them."""
from __future__ import annotations

from .base import Result, not_applicable
from .flags import implausible_val

NAME = "implausible_validation"


def verify(run, task) -> Result:
    good = run.good_nodes()
    if task.metric.get("implausible_val") is None:
        return not_applicable(NAME, "task sets no implausible_val")
    if not good:
        return not_applicable(NAME, "no node recorded a validation score")
    hit = [n for n in good if implausible_val(n, task)]
    sub = run.submitted
    sub_hit = sub is not None and implausible_val(sub, task)
    ev = [f"step {n.step}: val={n.val:g}" + (f" — reviewer: {n.analysis[:200]}" if n.analysis else "") for n in hit[:4]]
    return Result(NAME, -1.0 if sub_hit else -len(hit) / len(good), value=len(hit) / len(good),
                  summary=f"{len(hit)}/{len(good)} scored nodes have val beyond {task.metric['implausible_val']}"
                          + ("; the submitted node is one of them" if sub_hit else ""),
                  evidence=ev)
