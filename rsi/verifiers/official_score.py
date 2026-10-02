"""official_score: the MLE-bench grade of the submitted predictions.

Train tasks only.  For a test task the score is withheld here (applicable=False) so it can never reach memory
or the improver; held-out evaluation reads grade_report.txt itself."""
from __future__ import annotations

from .base import Result, not_applicable

NAME = "official_score"


def verify(run, task) -> Result:
    if task.role != "train":
        return not_applicable(NAME, "test task: official score withheld from the loop")
    if not run.grade or run.grade.get("score") is None:
        return not_applicable(NAME, "run not graded (no grade_report.txt or null score)")
    g = run.grade
    return Result(NAME, reward=g["score"], value=g["score"],
                  summary=f"official {task.metric['name']} = {g['score']:.5f} (median {g.get('median_threshold')}, "
                          f"bronze {g.get('bronze_threshold')})",
                  evidence=[f"valid_submission={g.get('valid_submission')}, above_median={g.get('above_median')}"])
