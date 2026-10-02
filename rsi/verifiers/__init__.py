"""All verifiers.  Each module has NAME and verify(run, task) -> Result; add one by adding a module here."""
from __future__ import annotations

from . import (implausible_validation, metric_mismatch, official_score, preprocessing_outside_cv, search_health,
               selection, submission_sanity, train_only_field)

ALL = [official_score, submission_sanity, train_only_field, implausible_validation, preprocessing_outside_cv,
       metric_mismatch, selection, search_health]


def verify_all(run, task) -> dict:
    """{"vector": {name: reward or None}, "results": [Result dicts]}.  Never collapsed into one number."""
    results = [m.verify(run, task) for m in ALL]
    return {"vector": {r.name: r.reward for r in results}, "results": [r.to_dict() for r in results]}
