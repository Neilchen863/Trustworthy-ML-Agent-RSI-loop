"""search_health: how much of the budget went into nodes that crashed.

value = buggy nodes / all nodes; reward = -value.  Evidence lists the most common exception types."""
from __future__ import annotations

from collections import Counter

from .base import Result, not_applicable

NAME = "search_health"


def verify(run, task) -> Result:
    if not run.nodes:
        return not_applicable(NAME, "run has no nodes")
    buggy = [n for n in run.nodes if n.is_buggy]
    exc = Counter(n.exc_type or "no exception (judged buggy)" for n in buggy)
    rate = len(buggy) / len(run.nodes)
    return Result(NAME, -rate, value=rate, summary=f"{len(buggy)}/{len(run.nodes)} nodes buggy",
                  evidence=[f"exceptions: {dict(exc.most_common(5))}"])
