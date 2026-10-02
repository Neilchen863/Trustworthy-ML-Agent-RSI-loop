"""train_only_field: does the search build on columns that the test set does not have?

value  = fraction of executed nodes whose code reads a train-only field (task.json `train_only_fields`)
reward = -value, or -1 if the submitted node reads one.
Not applicable when the task has no train-only fields.  See flags.train_only_uses for what counts."""
from __future__ import annotations

from collections import Counter

from .base import Result, not_applicable
from .flags import train_only_uses

NAME = "train_only_field"


def verify(run, task) -> Result:
    if not task.train_only_fields:
        return not_applicable(NAME, "task has no train-only fields")
    executed = [n for n in run.nodes if n.code.strip()]
    if not executed:
        return not_applicable(NAME, "run has no nodes with code")
    uses = {n.id: train_only_uses(n, task) for n in executed}
    hit = [n for n in executed if uses[n.id]]
    rate = len(hit) / len(executed)
    fields = Counter(f for n in hit for f in uses[n.id])
    sub = run.submitted
    sub_uses = uses.get(sub.id) if sub else None
    ev = [f"fields used (nodes): {dict(fields.most_common())}"]
    for n in hit[:3]:
        f, lines = next(iter(uses[n.id].items()))
        ev.append(f"step {n.step} (val={n.val}, buggy={n.is_buggy}): {f}: {lines[0]}")
    if sub_uses:
        ev.insert(0, f"SUBMITTED node (step {sub.step}) reads {sorted(sub_uses)}")
    return Result(NAME, -1.0 if sub_uses else -rate, value=rate,
                  summary=f"{len(hit)}/{len(executed)} nodes read a train-only field"
                          + ("; the submitted node is one of them" if sub_uses else ""),
                  evidence=ev)
