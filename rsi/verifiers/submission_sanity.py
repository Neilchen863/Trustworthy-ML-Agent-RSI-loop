"""submission_sanity: is the submitted file a usable prediction?

-1 when the submission is missing, graded invalid, contains empty values, or every prediction is the same
value (a constant scores exactly chance on AUC).  0 otherwise.  Only the submitted file is checked; AIDE
does not keep the other nodes' submissions."""
from __future__ import annotations

from .base import Result

NAME = "submission_sanity"


def verify(run, task) -> Result:
    if not run.submission:
        return Result(NAME, -1.0, summary="no submission/submission.csv (or it is empty)")
    if run.grade and run.grade.get("valid_submission") is False:
        return Result(NAME, -1.0, summary="the grader marked the submission invalid")
    cols = list(run.submission[0].keys())
    pred_col = task.label_column if task.label_column in cols else cols[-1]
    preds = [r.get(pred_col, "") for r in run.submission]
    empty = sum(1 for p in preds if p is None or str(p).strip() in ("", "nan", "NaN"))
    distinct = sorted(set(preds))
    ev = [f"{len(preds)} rows, column {pred_col!r}, {len(distinct)} distinct value(s), first: {distinct[:3]}"]
    if empty:
        return Result(NAME, -1.0, value=empty, summary=f"{empty} empty/NaN prediction(s)", evidence=ev)
    if len(distinct) == 1:
        return Result(NAME, -1.0, value=1, summary=f"every prediction is {distinct[0]} (constant)", evidence=ev)
    return Result(NAME, 0.0, value=len(distinct), summary="submission present and non-constant", evidence=ev)
