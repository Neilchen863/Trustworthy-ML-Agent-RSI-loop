"""preprocessing_outside_cv: a label-using preprocessing step is fitted on all training rows, and the same
rows are then scored by cross-validation or a CV search.

Static check (ast) of each scored node:
  - a supervised selector/resampler (SelectKBest, SelectPercentile, SelectFromModel, RFE, RFECV,
    SequentialFeatureSelector, SMOTE, ADASYN, BorderlineSMOTE, RandomOverSampler, RandomUnderSampler,
    TargetEncoder) is fitted directly with labels: `.fit(X, y)`, `.fit_transform(X, y)`, `.fit_resample(X, y)`;
  - and the code runs CV (cross_val_score / cross_validate / cross_val_predict / GridSearchCV /
    RandomizedSearchCV / a KFold-style `.split`).
Fitting the same object inside a Pipeline that CV refits per fold is fine and is not flagged.

value  = fraction of scored nodes flagged; reward = -value, or -1 if the submitted node is flagged.
The resulting optimism can be modest (e.g. 0.66 -> 0.78 AUC), which a fixed "implausible" threshold misses."""
from __future__ import annotations

import ast

from .base import Result, not_applicable

NAME = "preprocessing_outside_cv"
SUPERVISED = {"SelectKBest", "SelectPercentile", "SelectFromModel", "RFE", "RFECV", "SequentialFeatureSelector",
              "SMOTE", "ADASYN", "BorderlineSMOTE", "SVMSMOTE", "RandomOverSampler", "RandomUnderSampler",
              "TargetEncoder"}
CV_CALLS = {"cross_val_score", "cross_validate", "cross_val_predict", "GridSearchCV", "RandomizedSearchCV",
            "HalvingGridSearchCV"}
FIT = {"fit", "fit_transform", "fit_resample"}


def _name(f) -> str:
    return f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")


def leaky_fits(code: str) -> list:
    """Source lines where a supervised selector/resampler is fitted with labels, if the code also runs CV."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    lines = code.splitlines()
    sup_vars, uses_cv, hits = set(), False, []
    for n in ast.walk(tree):
        if isinstance(n, ast.Assign) and isinstance(n.value, ast.Call) and _name(n.value.func) in SUPERVISED:
            sup_vars |= {t.id for t in n.targets if isinstance(t, ast.Name)}
        if isinstance(n, ast.Call):
            if _name(n.func) in CV_CALLS:
                uses_cv = True
            if isinstance(n.func, ast.Attribute) and n.func.attr == "split" and len(n.args) >= 2:
                uses_cv = True
    for n in ast.walk(tree):
        if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in FIT
                and len(n.args) + len(n.keywords) >= 2):
            continue
        obj = n.func.value
        direct = isinstance(obj, ast.Call) and _name(obj.func) in SUPERVISED          # SelectKBest(...).fit(X, y)
        named = isinstance(obj, ast.Name) and obj.id in sup_vars                      # sel.fit_transform(X, y)
        if direct or named:
            hits.append(lines[n.lineno - 1].strip()[:160])
    return hits if uses_cv else []


def verify(run, task) -> Result:
    good = run.good_nodes()
    if not good:
        return not_applicable(NAME, "no node recorded a validation score")
    hits = {n.id: leaky_fits(n.code) for n in good}
    bad = [n for n in good if hits[n.id]]
    sub_bad = bool(hits.get(run.submitted_id))
    ev = [f"step {n.step} (val={n.val:g}): {hits[n.id][0]}" for n in bad[:5]]
    return Result(NAME, -1.0 if sub_bad else -len(bad) / len(good), value=len(bad) / len(good),
                  summary=f"{len(bad)}/{len(good)} scored nodes fit a label-using selector/resampler on all training "
                          "rows before scoring them with CV" + ("; the submitted node is one of them" if sub_bad else ""),
                  evidence=ev)
