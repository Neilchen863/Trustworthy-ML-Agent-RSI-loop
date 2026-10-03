"""Called by the fixed adapter after a node's code raised an exception.

event = {"interface": "on_exec_error/1", "exc_type": str, "exc_message": str,
         "traceback_tail": str (last lines of the execution output), "code": str (the node's code)}

Return None to leave the execution output unchanged, or a short string; the adapter appends it to the
node's execution output as "[harness note] <string>", which AIDE's reviewer and debugger read.
H0 returns None for every event, i.e. stock AIDE behaviour."""

import re


def diagnose(event):
    exc_type = event.get("exc_type", "") or ""
    msg = event.get("exc_message", "") or ""
    tail = event.get("traceback_tail", "") or ""
    text = msg + "\n" + tail

    if exc_type == "ValueError" and "Feature names are only supported if all input features have string names" in text:
        return (
            "Mixed column-name types caused sklearn to fail. After creating/concatenating feature DataFrames, make "
            "column names uniform before CV/fit, e.g. X.columns = X.columns.map(str) for both train and test. "
            "An even safer option is to avoid pandas here and use scipy.sparse.hstack / numpy arrays with consistent schema."
        )

    if exc_type == "TimeoutError":
        hints = []
        if re.search(r"RandomizedSearchCV|GridSearchCV|_search\.py|evaluate_candidates|totalling", text):
            hints.append("The timeout occurred inside hyperparameter search. Remove nested search or shrink it drastically (very small n_iter/cv), and prefer a single default/near-default model.")
        if ".toarray()" in event.get("code", ""):
            hints.append("Your code densifies sparse text features with .toarray(); keep TF-IDF sparse to reduce memory/time.")
        hints.append("Under this budget, a simple TF-IDF + LogisticRegression baseline is usually safer than stacking/tuning multiple tree models.")
        return " ".join(hints)[:2000]

    if exc_type == "ValueError" and "random_state has no effect since shuffle is False" in text:
        return (
            "StratifiedKFold was created with random_state but shuffle=False. Fix by either using "
            "StratifiedKFold(..., shuffle=True, random_state=42) or removing random_state."
        )

    return None
