"""The on_exec_error entry: boundary, isolation, failure handling, and offline reproduction of the audited errors."""
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fixtures import make_run, node  # noqa: E402
from rsi import candidate  # noqa: E402
from rsi import harness as hz  # noqa: E402
from rsi import harness_adapter as ha  # noqa: E402
from rsi.dynamics import extract  # noqa: E402
from rsi.run import load_run  # noqa: E402
from rsi.task import load_task  # noqa: E402

ROAP = load_task("random_acts_of_pizza")
H0 = hz.read(Path(__file__).resolve().parents[1] / "harness" / "H0")
EV = candidate.FIXED_EVENTS[0]


# ------------------------------------------------------------------ boundary
def test_hook_static_rules():
    ok = "import re\nfrom collections import Counter\ndef diagnose(event):\n    return None\n"
    assert hz.hook_problems(ok) == []
    for bad, why in [("import os\ndef diagnose(e):\n    return None\n", "import"),
                     ("import importlib\ndef diagnose(e):\n    return None\n", "import"),
                     ("from . import x\ndef diagnose(e):\n    return None\n", "import"),
                     ("def diagnose(e):\n    return open('/etc/passwd').read()\n", "open"),
                     ("def diagnose(e):\n    return ().__class__.__mro__\n", "dunder"),
                     ("def diagnose(e):\n    return eval('1')\n", "eval"),
                     ("def diagnose(e, f):\n    return None\n", "exactly one"),
                     ("def other(e):\n    return None\n", "no module-level"),
                     ("def diagnose(e:\n", "syntax")]:
        assert any(why in p for p in hz.hook_problems(bad)), (bad, hz.hook_problems(bad))


def test_tree_rejects_symlinks_and_extra_files(tmp_path):
    d = tmp_path / "v"
    hz.write(d, H0)
    assert hz.tree_problems(d) == []
    (d / "hooks" / "evil.py").write_text("x")
    os.symlink("/etc/passwd", d / "link")
    (d / "sub").mkdir()
    probs = hz.tree_problems(d)
    assert any("evil.py" in p for p in probs) and any("symlink" in p for p in probs) and any("sub/" in p for p in probs)


def test_validate_requires_hook_and_whitelist():
    assert any("missing hooks/on_exec_error.py" in p for p in hz.validate({k: v for k, v in H0.items() if k != hz.HOOK}))
    assert any("not part of the harness" in p for p in hz.validate({**H0, "hooks/other.py": ""}))


# ------------------------------------------------------------------ isolation and failure handling
def test_run_hook_isolation(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-leak")
    env_probe = b"import os\ndef diagnose(e):\n    return repr(sorted(os.environ))\n"   # bypasses static rules
    r = ha.run_hook(env_probe, EV)
    assert r["status"] == "ok" and "OPENAI_API_KEY" not in r["message"]
    target = tmp_path / "written.txt"
    writer = f"def diagnose(e):\n    open({str(target)!r}, 'w').write('x' * 10)\n    return 'wrote'\n".encode()
    ha.run_hook(writer, EV)
    assert not (target.exists() and target.read_text())          # RLIMIT_FSIZE=0: no bytes reach any file
    cwd = b"import os\ndef diagnose(e):\n    return os.getcwd()\n"
    assert "rsi_hook_" in ha.run_hook(cwd, EV)["message"]


@pytest.mark.parametrize("src,status", [
    (b"def diagnose(e):\n    raise RuntimeError('boom')\n", "error"),
    (b"def diagnose(e):\n    return 3\n", "error"),
    (b"def diagnose(e):\n    return 'x' * 5000\n", "invalid"),
    (b"def diagnose(e):\n    while True:\n        pass\n", "error"),       # killed by the CPU limit
    (b"import time\ndef diagnose(e):\n    time.sleep(30)\n", "timeout"),
    (b"syntax error here\n", "error"),
])
def test_faulty_hook_never_changes_output(tmp_path, src, status):
    stage = tmp_path / "h"
    files = {**H0, hz.HOOK: src.decode()}
    hz.write(stage, files)
    (stage / hz.MANIFEST).write_text(json.dumps(hz.manifest(files, None, None)))
    called = []

    class Agent:
        def parse_exec_result(self, node, res):
            called.append("".join(res.term_out))

    class N:
        id, code = "n1", "x"

    class R:
        term_out, exc_type, exc_info = ["Traceback\n"], "KeyError", {"args": ["a"]}

    log = tmp_path / "ev.jsonl"
    ha.HOOK_TIMEOUT_SAVED = ha.HOOK_TIMEOUT
    ha.apply(Agent, harness_dir=str(stage), log_path=str(log))
    res = R()
    res.term_out = ["Traceback\n"]
    if status == "timeout":                      # keep the test fast: the adapter passes HOOK_TIMEOUT to run_hook
        assert ha.run_hook(src, EV, timeout=1.0)["status"] == "timeout"
        return
    Agent().parse_exec_result(N(), res)
    call = [json.loads(l) for l in log.read_text().splitlines()][-1]
    assert called == ["Traceback\n"] and call["status"] == status and call["appended"] is False


def test_adapter_disabled_without_manifest_and_on_mismatch(tmp_path):
    class Agent:
        def parse_exec_result(self, node, res):
            return "orig"

    h = ha.apply(Agent, harness_dir=str(tmp_path / "none"), log_path=str(tmp_path / "a.jsonl"))
    assert h.manifest is None and not (tmp_path / "a.jsonl").exists()

    stage = tmp_path / "h"
    good = {**H0, hz.HOOK: "def diagnose(e):\n    return 'note'\n"}
    hz.write(stage, good)
    (stage / hz.MANIFEST).write_text(json.dumps(hz.manifest(H0, None, None)))      # manifest of other bytes

    class Agent2(Agent):
        pass

    class N:
        id, code = "n1", ""

    class R:
        exc_type, exc_info = "KeyError", None

    log = tmp_path / "b.jsonl"
    h = ha.apply(Agent2, harness_dir=str(stage), log_path=str(log))
    res = R()
    res.term_out = ["x"]
    Agent2().parse_exec_result(N(), res)
    events = [json.loads(l) for l in log.read_text().splitlines()]
    assert not h.enabled and events[0]["match"] is False and events[0]["fallback"] and len(events) == 1
    assert res.term_out == ["x"]


def test_adapter_reads_hook_once(tmp_path):
    """Edits to the staged hook after load have no effect (the run executes the verified bytes)."""
    stage = tmp_path / "h"
    files = {**H0, hz.HOOK: "def diagnose(e):\n    return 'v1'\n"}
    hz.write(stage, files)
    (stage / hz.MANIFEST).write_text(json.dumps(hz.manifest(files, None, None)))

    class Agent:
        def parse_exec_result(self, node, res):
            pass

    class N:
        id, code = "n", ""

    class R:
        exc_type, exc_info = "E", None

    ha.apply(Agent, harness_dir=str(stage), log_path=str(tmp_path / "l"))
    (stage / hz.HOOK).write_text("def diagnose(e):\n    return 'v2'\n")
    res = R()
    res.term_out = []
    Agent().parse_exec_result(N(), res)
    assert res.term_out == ["\n[harness note] v1\n"]


def test_candidate_rejects_unanswered_citation_and_accepts_answering_hook():
    base = dict(H0)
    ev = [{"evidence_id": "x:step01:error", "event": candidate.FIXED_EVENTS[2]}]
    silent = {**base, hz.HOOK: "def diagnose(e):\n    return None if e else 'x'\n"}
    assert not candidate.check_candidate(silent, base, "agent", ev, ["x:step01:error"])["passed"]
    answer = {**base, hz.HOOK: "def diagnose(e):\n    return 'nltk' if 'nltk' in e['code'] else None\n"}
    r = candidate.check_candidate(answer, base, "agent", ev, ["x:step01:error"])
    assert r["passed"] and r["checks"][-1]["detail"]["appended"]
    assert candidate.check_candidate(base, base, "agent")["passed"]                  # H0 itself passes


# ------------------------------------------------------------------ offline reproduction of the audited errors
def _exec_node(code: str):
    """Run code like AIDE's interpreter would and return (exc_type, exc_info, term_out)."""
    import traceback
    try:
        exec(compile(code, "runfile.py", "exec"), {})
    except Exception as exc:          # noqa: BLE001
        return type(exc).__name__, {"args": [str(a) for a in exc.args]}, traceback.format_exc()
    return None, None, ""


def test_reproduce_function_transformer_keyerror(tmp_path):
    """j1500133 steps 7/17/20/21: a FunctionTransformer given one column name receives a Series and indexes it by
    column name -> KeyError inside ColumnTransformer."""
    pytest.importorskip("sklearn")
    code = ("import pandas as pd\nfrom sklearn.compose import ColumnTransformer\n"
            "from sklearn.preprocessing import FunctionTransformer\n"
            "df = pd.DataFrame({'request_text_edit_aware': ['a b', 'c'], 'n': [1, 2]})\n"
            "def feats(X):\n    return X['request_text_edit_aware'].str.len().to_frame()\n"
            "ct = ColumnTransformer([('t', FunctionTransformer(feats), 'request_text_edit_aware')])\n"
            "ct.fit_transform(df)\n")
    exc, info, out = _exec_node(code)
    assert exc == "KeyError" and "request_text_edit_aware" in info["args"][0]
    run = make_run(tmp_path / "r", [node(0, code=code, out=out, exc=exc, exc_info=info)])
    dyn, evidence = extract(load_run(run), ROAP)
    assert dyn["nodes"][0]["error"]["signature"].startswith("KeyError: request_text_edit_aware @ ")
    assert evidence[0]["event"]["exc_type"] == "KeyError"


def test_reproduce_nltk_resource_missing(tmp_path, monkeypatch):
    """j1500133 steps 4/16/23: NLTK resources are not installed in the container and cannot be downloaded."""
    nltk = pytest.importorskip("nltk")
    monkeypatch.setattr(nltk.data, "path", [str(tmp_path / "empty")])
    code = "import nltk\nfrom nltk.sentiment import SentimentIntensityAnalyzer\nSentimentIntensityAnalyzer()\n"
    exc, info, out = _exec_node(code)
    assert exc == "LookupError" and "vader_lexicon" in info["args"][0]
    hook = ("def diagnose(event):\n    if event['exc_type'] == 'LookupError' and 'nltk' in event['code']:\n"
            "        return 'NLTK data is not available in this environment.'\n    return None\n")
    r = ha.run_hook(hook.encode(), ha.make_event(exc, info, out, code))
    assert r["status"] == "ok" and r["message"].startswith("NLTK data")
