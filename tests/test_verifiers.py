import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fixtures import make_run, node  # noqa: E402
from rsi.run import Node, load_run  # noqa: E402
from rsi.task import load_task  # noqa: E402
from rsi.verifiers import verify_all  # noqa: E402
from rsi.verifiers.flags import train_only_uses  # noqa: E402

ROAP = load_task("random_acts_of_pizza")
INSULTS = load_task("insults")
LEAK = 'df["f"] = (df["requester_user_flair"] == "shroom").astype(int)\n'


def vec(tmp_path, nodes, task=ROAP, **kw):
    return verify_all(load_run(make_run(tmp_path / "run", nodes, **kw)), task)["vector"]


def test_clean_run(tmp_path):
    v = vec(tmp_path, [node(0, val=0.62), node(1, val=0.60)])
    assert v == {"official_score": 0.6, "submission_sanity": 0.0, "train_only_field": 0.0,
                 "implausible_validation": 0.0, "preprocessing_outside_cv": 0.0, "metric_mismatch": 0.0,
                 "selection": 0.0, "search_health": 0.0}


def test_leak_run_like_j1460465(tmp_path):
    nodes = [node(0, val=0.62), node(1, code=LEAK, val=1.0), node(2, code=LEAK, buggy=True, exc="KeyError")]
    v = vec(tmp_path, nodes, submitted="n1", preds=(0.0987, 0.0987, 0.0987), score=0.5)
    assert v["submission_sanity"] == -1.0           # constant
    assert v["train_only_field"] == -1.0            # submitted node reads the field
    assert v["implausible_validation"] == -1.0      # submitted val 1.0
    assert v["selection"] == -1.0                   # flagged node chosen over trusted n0
    assert abs(v["search_health"] + 1 / 3) < 1e-9


def test_train_only_rate_when_not_submitted(tmp_path):
    v = vec(tmp_path, [node(0, val=0.62), node(1, code=LEAK, val=0.7), node(2), node(3)])
    assert v["train_only_field"] == -0.25


def test_removal_and_fallback_are_not_uses():
    cases = {
        'df = df.drop(columns=[\n    "requester_user_flair",\n])\n': False,
        'if "requester_user_flair" in df.columns:\n    pass\n': False,
        'col = "request_text_edit_aware" if ok else "request_text"\n': False,
        'drop_cols = ["post_was_edited"]\n': False,
        'X = df["requester_user_flair"]\n': True,
        'feats = ["request_text_edit_aware"]\n': False,       # a test-side field, not request_text
        'for t in ["request_text"]:\n    df[f"{t}_sent"] = 1\n': False,          # name prefix only
        'cols = [f"{t}_s" for t in ["request_text"]]\n': False,
        'for t in ["request_text"]:\n    x = df[t]\n': True,                      # a real column read
    }
    for code, used in cases.items():
        n = Node("n0", 0, None, code, "", "", False, None, 0.6, "")
        assert bool(train_only_uses(n, ROAP)) is used, code


def test_metric_mismatch_five_fold(tmp_path):
    out = "Mean AUC score (5-fold CV): 0.6323\n"
    v = vec(tmp_path, [node(0, val=0.6323, out=out), node(1, val=5.0, out=out)], submitted="n0")
    assert v["metric_mismatch"] == -0.5
    assert v["implausible_validation"] == 0.0         # out of range is a mismatch, not implausible


def test_metric_matches_rounded_print(tmp_path):
    v = vec(tmp_path, [node(0, val=0.623456, out="AUC: 0.62\n")])
    assert v["metric_mismatch"] == 0.0
    v = vec(tmp_path / "b", [node(0, val=0.62, out="folds: 1 2 3\n")])   # integers never match loosely
    assert v["metric_mismatch"] == -1.0


def test_selection_gap(tmp_path):
    v = vec(tmp_path, [node(0, val=0.60), node(1, val=0.65)], submitted="n0")
    assert abs(v["selection"] + 0.05) < 1e-9


def test_buggy_submission(tmp_path):
    v = vec(tmp_path, [node(0, val=0.6), node(1, buggy=True)], submitted="n1")
    assert v["selection"] == -1.0


def test_test_task_withholds_score(tmp_path):
    v = vec(tmp_path, [node(0)], task=INSULTS)
    assert v["official_score"] is None and v["train_only_field"] is None


def test_preprocessing_outside_cv():
    from rsi.verifiers.preprocessing_outside_cv import leaky_fits
    leak = "sel = SelectKBest(k=10)\nX2 = sel.fit_transform(X, y)\ns = cross_val_score(m, X2, y, cv=5)\n"
    smote = "Xb, yb = SMOTE().fit_resample(X, y)\ng = GridSearchCV(m, p, cv=5)\ng.fit(Xb, yb)\n"
    piped = "p = Pipeline([('s', SelectKBest(k=10)), ('m', m)])\ns = cross_val_score(p, X, y, cv=5)\n"
    no_cv = "sel = SelectKBest(k=10)\nX2 = sel.fit_transform(X, y)\nm.fit(X2, y)\n"
    assert leaky_fits(leak) and leaky_fits(smote)
    assert not leaky_fits(piped) and not leaky_fits(no_cv)


def test_preprocessing_outside_cv_classification():
    from rsi.verifiers.preprocessing_outside_cv import classify
    per_fold = ("for tr, va in kf.split(X, y):\n    sel = SelectKBest(k=5)\n"
                "    Xt = sel.fit_transform(X[tr], y[tr])\n    m.fit(Xt, y[tr])\n")
    refit = ("s = cross_val_score(Pipeline([('s', SelectKBest()), ('m', m)]), X, y, cv=5)\n"
             "sel = SelectKBest(k=5)\nXf = sel.fit_transform(X, y)\nm.fit(Xf, y)\n")
    chained = ("sel = SelectKBest(k=5)\nsel.fit(X, y)\nX2 = sel.transform(X)\nX3 = hstack([X2, Z])\n"
               "s = cross_val_score(m, X3, y, cv=5)\n")
    search = ("Xb, yb = SMOTE().fit_resample(X, y)\ng = GridSearchCV(m, p, cv=5)\ng.fit(Xb, yb)\n")
    untraced = ("sel = SelectKBest(k=5)\nXs = sel.fit_transform(X, y)\nX_all = build(Xs_name)\n"
                "s = cross_val_score(m, X_all, y, cv=5)\n")
    assert classify(per_fold) == {"confirmed": [], "suspect": []}
    assert classify(refit) == {"confirmed": [], "suspect": []}
    assert classify(chained)["confirmed"] and classify(search)["confirmed"]
    u = classify(untraced)
    assert not u["confirmed"] and u["suspect"]
