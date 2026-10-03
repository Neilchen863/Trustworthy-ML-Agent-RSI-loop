"""preprocessing_outside_cv: a label-using preprocessing step is fitted on all training rows and its output
is then scored by cross-validation or a CV search.

Static check (ast) of each scored node.  A *supervised fit* is `.fit(X, y)`, `.fit_transform(X, y)` or
`.fit_resample(X, y)` on SelectKBest, SelectPercentile, SelectFromModel, RFE, RFECV,
SequentialFeatureSelector, SMOTE, ADASYN, BorderlineSMOTE, SVMSMOTE, RandomOverSampler, RandomUnderSampler
or TargetEncoder.  A *CV use* is cross_val_score / cross_validate / cross_val_predict, `.fit(...)` on a
GridSearchCV / RandomizedSearchCV / HalvingGridSearchCV object, or a loop over a KFold-style `.split(...)`.

Each supervised fit is classified:
  ignored    inside a loop over `.split(...)` (fitted per fold), or after every CV use (final refit), or
             inside a Pipeline (it is not called directly then)
  confirmed  its output reaches a later CV use through assignments (`X2 = sel.fit_transform(X, y)`,
             `X3 = hstack([X2, ...])`, `cross_val_score(m, X3, y)`), or the fitted object's `.transform`
             output does
  suspect    before a CV use, but the data flow cannot be traced (e.g. it crosses a function call)

value  = fraction of scored nodes with a confirmed fit; reward = -value, or -1 if the submitted node has one.
Suspect nodes are listed in the summary and evidence but do not lower the reward."""
from __future__ import annotations

import ast

from .base import Result, not_applicable

NAME = "preprocessing_outside_cv"
SUPERVISED = {"SelectKBest", "SelectPercentile", "SelectFromModel", "RFE", "RFECV", "SequentialFeatureSelector",
              "SMOTE", "ADASYN", "BorderlineSMOTE", "SVMSMOTE", "RandomOverSampler", "RandomUnderSampler",
              "TargetEncoder"}
CV_FUNCS = {"cross_val_score", "cross_validate", "cross_val_predict"}
SEARCH = {"GridSearchCV", "RandomizedSearchCV", "HalvingGridSearchCV"}
FIT = {"fit", "fit_transform", "fit_resample"}


def _name(f) -> str:
    return f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")


def _loads(node) -> set:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)} if node is not None else set()


def _targets(stmt) -> set:
    out = set()
    tgts = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
    for t in tgts:
        out |= {n.id for n in ast.walk(t) if isinstance(n, ast.Name)}
    return out


def _is_split_loop(loop) -> bool:
    return any(isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute) and c.func.attr == "split"
               for c in ast.walk(loop.iter))


def classify(code: str) -> dict:
    """{"confirmed": [lines], "suspect": [lines]} for the supervised fits in `code`."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return {"confirmed": [], "suspect": []}
    lines = code.splitlines()
    parent = {c: p for p in ast.walk(tree) for c in ast.iter_child_nodes(p)}

    def ancestors(n):
        while n in parent:
            n = parent[n]
            yield n

    sup_vars, search_vars = set(), set()
    assigns = sorted((n for n in ast.walk(tree) if isinstance(n, (ast.Assign, ast.AnnAssign)) and n.value is not None),
                     key=lambda n: n.lineno)
    for a in assigns:
        if isinstance(a.value, ast.Call):
            if _name(a.value.func) in SUPERVISED:
                sup_vars |= _targets(a)
            if _name(a.value.func) in SEARCH:
                search_vars |= _targets(a)

    cv_uses = []                                      # (lineno, names the CV evaluates)
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            fn = _name(n.func)
            if fn in CV_FUNCS:
                cv_uses.append((n.lineno, set().union(*[_loads(x) for x in n.args[1:]],
                                                      *[_loads(k.value) for k in n.keywords])))
            elif isinstance(n.func, ast.Attribute) and n.func.attr == "fit" and (
                    (isinstance(n.func.value, ast.Name) and n.func.value.id in search_vars)
                    or (isinstance(n.func.value, ast.Call) and _name(n.func.value.func) in SEARCH)):
                cv_uses.append((n.lineno, set().union(*[_loads(x) for x in n.args])))
        if isinstance(n, ast.For) and _is_split_loop(n):
            cv_uses.append((n.lineno, _loads(n.iter) | set().union(*[_loads(b) for b in n.body])))
    if not cv_uses:
        return {"confirmed": [], "suspect": []}
    last_cv = max(l for l, _ in cv_uses)

    out = {"confirmed": [], "suspect": []}
    for n in ast.walk(tree):
        if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in FIT
                and len(n.args) + len(n.keywords) >= 2):
            continue
        obj = n.func.value
        if not ((isinstance(obj, ast.Call) and _name(obj.func) in SUPERVISED)
                or (isinstance(obj, ast.Name) and obj.id in sup_vars)):
            continue
        if any(isinstance(a, ast.For) and _is_split_loop(a) for a in ancestors(n)):
            continue                                                   # fitted inside a fold loop
        if n.lineno > last_cv:
            continue                                                   # final refit after evaluation
        stmt = next((a for a in ancestors(n) if isinstance(a, ast.stmt)), None)
        tainted = _targets(stmt) if isinstance(stmt, (ast.Assign, ast.AnnAssign)) else set()
        fitted_obj = obj.id if isinstance(obj, ast.Name) else None
        for a in assigns:                                              # propagate in source order
            if a.lineno <= n.lineno:
                continue
            uses_transform = fitted_obj and any(
                isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute) and c.func.attr == "transform"
                and isinstance(c.func.value, ast.Name) and c.func.value.id == fitted_obj for c in ast.walk(a.value))
            if uses_transform or (_loads(a.value) & tainted):
                tainted |= _targets(a)
        line = lines[n.lineno - 1].strip()[:160]
        if any(l > n.lineno and names & tainted for l, names in cv_uses):
            out["confirmed"].append(line)
        else:
            out["suspect"].append(line)
    return out


def leaky_fits(code: str) -> list:
    """Confirmed fits only (used by flags.flagged)."""
    return classify(code)["confirmed"]


def verify(run, task) -> Result:
    good = run.good_nodes()
    if not good:
        return not_applicable(NAME, "no node recorded a validation score")
    res = {n.id: classify(n.code) for n in good}
    conf = [n for n in good if res[n.id]["confirmed"]]
    susp = [n for n in good if not res[n.id]["confirmed"] and res[n.id]["suspect"]]
    sub = res.get(run.submitted_id, {"confirmed": [], "suspect": []})
    ev = [f"step {n.step} (val={n.val:g}) CONFIRMED: {res[n.id]['confirmed'][0]}" for n in conf[:4]]
    ev += [f"step {n.step} (val={n.val:g}) SUSPECT, flow not traced: {res[n.id]['suspect'][0]}" for n in susp[:2]]
    summary = (f"{len(conf)}/{len(good)} scored nodes feed a label-using selector/resampler fitted on all training "
               f"rows into CV" + (f"; {len(susp)} more suspect (flow not traced, not counted)" if susp else "")
               + ("; the submitted node is confirmed" if sub["confirmed"] else
                  "; the submitted node is suspect" if sub["suspect"] else ""))
    return Result(NAME, -1.0 if sub["confirmed"] else -len(conf) / len(good), value=len(conf) / len(good),
                  summary=summary, evidence=ev)
