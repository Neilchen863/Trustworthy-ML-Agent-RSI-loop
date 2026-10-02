"""metric_mismatch: the validation number AIDE recorded is not what the code printed.

Catches e.g. 'Mean AUC (5-fold CV): 0.62' recorded as 5.0.
value  = fraction of checkable scored nodes whose recorded metric fails flags.metric_mismatch
reward = -value, or -1 if the submitted node is one of them."""
from __future__ import annotations

from .base import Result, not_applicable
from .flags import metric_mismatch

NAME = "metric_mismatch"


def verify(run, task) -> Result:
    checkable = [n for n in run.good_nodes() if n.term_out.strip() or metric_mismatch(n, task)]
    if not checkable:
        return not_applicable(NAME, "no scored node kept its printed output")
    bad = [(n, metric_mismatch(n, task)) for n in checkable]
    bad = [(n, why) for n, why in bad if why]
    sub_bad = any(n.id == run.submitted_id for n, _ in bad)
    return Result(NAME, -1.0 if sub_bad else -len(bad) / len(checkable), value=len(bad) / len(checkable),
                  summary=f"{len(bad)}/{len(checkable)} checkable nodes recorded a metric the output does not show"
                          + ("; the submitted node is one of them" if sub_bad else ""),
                  evidence=[f"step {n.step}: {why}; output tail: {n.term_out.strip()[-160:]!r}" for n, why in bad[:4]])
