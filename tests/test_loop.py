import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fixtures import make_harness_run, make_run, node  # noqa: E402
from rsi import harness as hz  # noqa: E402
from rsi.backend import run_env  # noqa: E402
from rsi.improver import Improver  # noqa: E402
from rsi.llm import ScriptedLLM  # noqa: E402
from rsi.loop import Loop, LoopError  # noqa: E402
from rsi.task import load_task  # noqa: E402

ROAP = load_task("random_acts_of_pizza")
INSULTS = load_task("insults")
NOTES = "Drop columns that are missing from the test set; do not fill them with defaults.\n"
NLTK_INFO = {"args": ["\n****\n  Resource \x1b[93mpunkt_tab\x1b[0m not found.\n  Please use the NLTK Downloader\n****\n"]}
NLTK_CODE = "import nltk\nfrom nltk.tokenize import word_tokenize\ntokens = word_tokenize(text)\n"
NLTK_OUT = ('Traceback (most recent call last):\n  File "runfile.py", line 3, in <module>\n'
            '    tokens = word_tokenize(text)\nLookupError: \n  Resource punkt_tab not found.\n')
HOOK = ('def diagnose(event):\n'
        '    if event["exc_type"] == "LookupError" and "nltk" in event["code"]:\n'
        '        return "NLTK data files are not installed here and cannot be downloaded (no network, read-only '
        'home). Use tokenizers that need no downloaded data, e.g. sklearn CountVectorizer/TfidfVectorizer."\n'
        '    return None\n')


def proposal(ids, paths, summary="s"):
    return {"evidence_ids": ids, "observation": "o", "hypothesis": "h", "changed_paths": paths,
            "expected_behavior": "e", "regression_risks": "r", "summary": summary}


def nltk_nodes():
    return [node(0, val=0.6), node(1, code=NLTK_CODE, out=NLTK_OUT, exc="LookupError", exc_info=NLTK_INFO),
            node(2, code=NLTK_CODE, out=NLTK_OUT, exc="LookupError", exc_info=NLTK_INFO)]


def round0(tmp_path, freeze=None):
    loop = Loop(tmp_path / "s")
    if freeze:
        from rsi import method
        method.freeze(loop.state, freeze)
    loop.init()
    rec = loop.collect(ROAP, 0, make_harness_run(tmp_path / "r0", nltk_nodes(), loop.harness_dir(0),
                                                 parents={"n1": "n0", "n2": "n1"}))
    return loop, rec


def test_round_records_dynamics_receipt_and_evidence(tmp_path):
    loop, rec = round0(tmp_path)
    rd = loop.round_dir(ROAP, 0)
    receipt = json.loads((rd / "harness_receipt.json").read_text())
    assert receipt["ok"] and receipt["calls"]["n"] == 2 and receipt["calls"]["appended"] == 0     # H0: stock
    s = rec["dynamics"]["summary"]
    assert s["n_error"] == 2 and s["n_errors_first_in_lineage"] == 1 and s["n_errors_suspected_propagation"] == 1
    ids = [e["evidence_id"] for e in rec["dynamics"]["evidence_index"]]
    assert "r0:step01:error" in ids and "r0:step02:propagation" in ids and "r0:step01:hook1" in ids
    ev = {e["evidence_id"]: e for e in json.loads((rd / "evidence.json").read_text())}
    assert ev["r0:step01:error"]["event"]["exc_type"] == "LookupError"                         # replayable
    assert ev["r0:step01:error"]["source"]["pointer"] == "/nodes/1/_term_out"
    node1 = next(n for n in json.loads((rd / "dynamics.json").read_text())["nodes"] if n["step"] == 1)
    assert node1["tokens"] is None and node1["tokens_missing_reason"] == "not_attributed"
    assert rec["delivery"]["ok"] and len(loop.memory.records()) == 1


def test_hook_edit_published_and_next_run_executes_it(tmp_path):
    loop, _ = round0(tmp_path)
    llm = ScriptedLLM([[("read_evidence", {"evidence_id": "r0:step01:error"})],
                       [("write_file", {"path": "harness/hooks/on_exec_error.py", "content": HOOK})],
                       [("check", {"evidence_ids": ["r0:step01:error"]})],
                       [("finish", proposal(["r0:step01:error", "r0:step02:propagation"], [hz.HOOK]))]])
    out = loop.improve(ROAP, 0, Improver(llm))
    assert out["outcome"] == "edited" and out["published"], out
    tool_msgs = [m["content"] for m in llm.seen[-1] if m["role"] == "tool"]
    assert "Resource" in tool_msgs[0] and "hook_input_event" in tool_msgs[0]           # evidence excerpt was read
    man = hz.read_manifest(loop.harness_dir(1))
    assert man["parent_digest"] == hz.digest(loop.harness(0)) and man["files"][hz.HOOK]
    assert (loop.state / "candidates" / out["candidate"] / "proposal.json").is_file()
    assert all(ok for _, ok in out["controller_check"])
    run1 = make_harness_run(tmp_path / "r1", nltk_nodes(), loop.harness_dir(1), parents={"n1": "n0", "n2": "n0"})
    rec1 = loop.collect(ROAP, 1, run1)
    receipt = json.loads((loop.round_dir(ROAP, 1) / "harness_receipt.json").read_text())
    assert receipt["ok"] and receipt["calls"]["appended"] == 2
    assert receipt["actual"]["loaded_files"] == man["files"]
    journal = json.loads((run1 / "logs" / "journal.json").read_text())
    assert "[harness note] NLTK data files" in journal["nodes"][1]["_term_out"][0]        # what AIDE reads
    assert rec1["dynamics"]["summary"]["harness_calls_appended"] == 2


def test_failed_candidate_keeps_version_and_is_recorded(tmp_path):
    loop, _ = round0(tmp_path)
    silent = "def diagnose(event):\n    return None if event else ''\n"            # changed, answers nothing
    llm = ScriptedLLM([[("write_file", {"path": "harness/hooks/on_exec_error.py", "content": silent})],
                       [("check", {"evidence_ids": ["r0:step01:error"]})],
                       [("check", {"evidence_ids": ["r0:step01:error"]})],
                       [("finish", proposal(["r0:step01:error"], [hz.HOOK]))]])
    out = loop.improve(ROAP, 0, Improver(llm))
    assert out["outcome"] == "check_failed" and not out["published"]
    assert hz.read(loop.harness_dir(1)) == hz.read(loop.harness_dir(0))
    assert hz.read_manifest(loop.harness_dir(1))["copy_of"] == "H0"
    cdir = loop.state / "candidates" / out["candidate"]
    assert "return None if event" in (cdir / "harness" / hz.HOOK).read_text()
    assert json.loads((cdir / "validation.json").read_text())["outcome"] == "check_failed"
    assert len(llm.seen) == 3                                                          # session ended at check 2


def test_controller_rechecks_what_the_improver_claims(tmp_path, monkeypatch):
    from rsi import candidate
    loop, _ = round0(tmp_path)
    llm = ScriptedLLM([[("write_file", {"path": "harness/hooks/on_exec_error.py", "content": HOOK})],
                       [("finish", proposal(["r0:step01:error"], [hz.HOOK]))]])
    real = candidate.check_candidate
    calls = []

    def flaky(*a, **k):                     # first call (improver's) passes, the controller's re-check fails
        calls.append(1)
        r = real(*a, **k)
        return r if len(calls) == 1 else {"passed": False, "checks": [{"check": "x", "passed": False, "detail": "x"}]}
    monkeypatch.setattr(candidate, "check_candidate", flaky)
    out = loop.improve(ROAP, 0, Improver(llm))
    assert out["outcome"] == "rejected" and not out["published"] and len(calls) == 2
    assert hz.read(loop.harness_dir(1)) == hz.read(loop.harness_dir(0))


def test_finish_requires_structured_proposal(tmp_path):
    loop, _ = round0(tmp_path)
    llm = ScriptedLLM([[("write_file", {"path": "harness/notes.md", "content": NOTES})],
                       [("finish", {**proposal(["nope"], ["notes.md"]), "hypothesis": ""})]])
    out = loop.improve(ROAP, 0, Improver(llm, max_steps=2))
    assert out["outcome"] == "unfinished"
    assert "unknown evidence ids" in llm.seen[-1][-1]["content"] and "hypothesis is empty" in llm.seen[-1][-1]["content"]


def test_notes_only_change_and_delivery(tmp_path):
    loop, _ = round0(tmp_path)
    llm = ScriptedLLM([[("write_file", {"path": "harness/notes.md", "content": NOTES})],
                       [("finish", proposal(["r0:verifier:search_health:1"], ["harness/notes.md"]))]])
    out = loop.improve(ROAP, 0, Improver(llm))
    assert out["outcome"] == "edited" and hz.read(loop.harness_dir(1))["notes.md"] == NOTES
    bad = make_harness_run(tmp_path / "r1bad", [node(0)], loop.harness_dir(1))          # notes not delivered
    with pytest.raises(LoopError, match="did not reach"):
        loop.collect(ROAP, 1, bad)
    assert len(loop.memory.records()) == 1 and (loop.round_dir(ROAP, 1) / "reward.json").is_file()


def test_run_without_adapter_is_refused(tmp_path):
    loop = Loop(tmp_path / "s")
    loop.init()
    with pytest.raises(LoopError, match="adapter load event"):
        loop.collect(ROAP, 0, make_run(tmp_path / "r0", [node(0)]))
    assert loop.memory.records() == []


def test_reused_old_run_is_marked(tmp_path):
    loop = Loop(tmp_path / "s")
    loop.init()
    rec = loop.collect(ROAP, 0, make_run(tmp_path / "r0", nltk_nodes()), reused="j1500133, .state-accept H1")
    assert rec["provenance"].startswith("j1500133") and rec["dynamics_source"].startswith("reused")
    assert json.loads((loop.round_dir(ROAP, 0) / "harness_receipt.json").read_text())["applicable"] is False


def test_no_change_copies_harness(tmp_path):
    loop, _ = round0(tmp_path)
    out = loop.improve(ROAP, 0, Improver(ScriptedLLM([[("finish", proposal([], [], "no evidence for a change"))]])))
    assert out["outcome"] == "no_change" and hz.read(loop.harness_dir(1)) == hz.read(loop.harness_dir(0))


def test_improver_cannot_write_outside_harness(tmp_path):
    loop, _ = round0(tmp_path)
    llm = ScriptedLLM([[("write_file", {"path": "context/memory.jsonl", "content": "x"})],
                       [("write_file", {"path": "harness/../rsi/loop.py", "content": "x"})],
                       [("write_file", {"path": "harness/config.json", "content": '{"tree_topk": 99}'})],
                       [("finish", proposal(["r0:step01:error"], ["config.json"]))]])
    out = loop.improve(ROAP, 0, Improver(llm, max_steps=4))
    assert out["outcome"] == "unfinished"                            # finish's check failed (invalid config)
    tool_msgs = [m["content"] for m in llm.seen[-1] if m["role"] == "tool"]
    assert tool_msgs[0].startswith("error: only") and tool_msgs[1].startswith("error: only")
    assert hz.read(loop.harness_dir(1)) == hz.read(loop.harness_dir(0))


def test_test_task_never_reaches_memory_or_improve(tmp_path):
    loop = Loop(tmp_path / "s")
    loop.init()
    rec = loop.collect(INSULTS, 0, make_run(tmp_path / "r0", [node(0)], score=0.9), check_delivery=False)
    assert rec["vector"]["official_score"] is None and loop.memory.records() == []
    with pytest.raises(LoopError, match="test task"):
        loop.improve(INSULTS, 0, Improver(ScriptedLLM([])))


def test_validate_and_env():
    files = hz.read(Path(__file__).resolve().parents[1] / "harness" / "H0")
    assert hz.validate(files) == [] and sorted(files) == sorted(hz.FILES)
    env = run_env(ROAP, files)
    assert env["AIDE_SELECTION_MODE"] == "agent" and env["AIDE_TEST_FEEDBACK"] == "0"
    assert "PROMPT_VARIANT" not in env and "AIDE_SUB_STATS" not in env
    cfg = json.loads(files["config.json"])
    cfg["submission_profile"] = True
    env = run_env(ROAP, {**files, "notes.md": NOTES, "config.json": json.dumps(cfg)})
    assert env["AIDE_SUB_STATS"] == "1" and env["PROMPT_VARIANT"] == hz.notes_variant(NOTES)
    assert hz.validate({**files, "x.py": ""})


def test_other_mode_keys_are_frozen(tmp_path):
    loop, _ = round0(tmp_path)
    cfg = json.loads(hz.read(loop.harness_dir(0))["config.json"])
    cfg["debug_prob"] = 0.5                                       # rule-mode key, task runs in agent mode
    llm = ScriptedLLM([[("write_file", {"path": "harness/config.json", "content": json.dumps(cfg)})],
                       [("finish", proposal(["r0:step01:error"], ["config.json"]))]])
    out = loop.improve(ROAP, 0, Improver(llm, max_steps=2))
    assert out["outcome"] == "unfinished"
    assert "debug_prob" in llm.seen[-1][-1]["content"]


def test_replicate_never_writes_memory(tmp_path):
    loop, _ = round0(tmp_path)
    rec = loop.collect(ROAP, 0, make_harness_run(tmp_path / "r0b", [node(0)], loop.harness_dir(0), score=0.7), rep=2)
    assert rec["replicate"] == 2 and len(loop.memory.records()) == 1
    assert (loop.state / "rounds" / ROAP.name / "round_00_r2" / "reward.json").is_file()


def test_task_notes_precede_harness_notes():
    from rsi.backend import rendered_notes
    files = hz.read(Path(__file__).resolve().parents[1] / "harness" / "H0")
    assert rendered_notes(ROAP, files) == files["notes.md"]                   # ROAP: byte-identical to notes.md
    odd = {**files, "notes.md": "x \n"}
    assert rendered_notes(ROAP, odd) == "x \n"
    text = rendered_notes(INSULTS, {**files, "notes.md": NOTES})
    assert text.startswith("Submission format") and text.rstrip().endswith(NOTES.strip())
    assert run_env(INSULTS, files)["PROMPT_VARIANT"] == hz.notes_variant(rendered_notes(INSULTS, files))


def test_frozen_method_blocks_changes_but_not_harness_versions(tmp_path, monkeypatch):
    from rsi import method
    loop, rec = round0(tmp_path, freeze="scripted")
    assert rec["method"] == method.frozen(loop.state)["digest"]
    assert hz.read_manifest(loop.harness_dir(0))["method_digest"] == rec["method"]
    llm = ScriptedLLM([[("write_file", {"path": "harness/hooks/on_exec_error.py", "content": HOOK})],
                       [("finish", proposal(["r0:step01:error"], [hz.HOOK]))]])
    assert loop.improve(ROAP, 0, Improver(llm))["published"]               # a new version is not a method change
    assert method.check(loop.state) == rec["method"]
    with pytest.raises(method.MethodError, match="improver_model"):
        method.check(loop.state, "gpt-4o")
    real = method.describe
    for key in ("verifier_digest", "harness_digest", "observation_digest", "tests_digest", "task_digest"):
        monkeypatch.setattr(method, "describe", lambda m, o=None, k=key: {**real(m, o), k: "changed"})
        with pytest.raises(method.MethodError, match=key):
            loop.collect(ROAP, 1, make_harness_run(tmp_path / f"r1{key}", [node(0)], loop.harness_dir(1)))


def test_published_version_is_immutable(tmp_path):
    loop, _ = round0(tmp_path)
    with pytest.raises(hz.HarnessError, match="never overwritten"):
        hz.publish(loop.harness_dir(0), loop.harness(0), hz.read_manifest(loop.harness_dir(0)))
    p = loop.harness_dir(0) / "notes.md"
    p.chmod(0o644)
    p.write_text("tampered\n")
    with pytest.raises(LoopError, match="differs from its manifest"):
        loop.harness(0)


def test_acceptance_harness_tool_offline_chain(tmp_path, capsys):
    """Full offline chain: evidence -> candidate -> checks -> H1 -> run through the adapter -> A-D pass."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    import acceptance_harness
    loop, _ = round0(tmp_path)
    llm = ScriptedLLM([[("write_file", {"path": "harness/hooks/on_exec_error.py", "content": HOOK})],
                       [("finish", proposal(["r0:step01:error"], [hz.HOOK]))]])
    loop.improve(ROAP, 0, Improver(llm))
    run1 = make_harness_run(tmp_path / "r1", nltk_nodes(), loop.harness_dir(1), parents={"n1": "n0", "n2": "n1"})
    (run1 / "logs" / "aide.verbose.log").write_text("debug prompt ... [harness note] NLTK data files ...\n")
    loop.collect(ROAP, 1, run1)
    rc = acceptance_harness.main(["--state", str(loop.state), "--task", ROAP.name, "--round", "0"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["ok"], json.dumps(out, indent=1)[:3000]
    d = out["report"][3]["facts"]
    assert d["hook_calls_appended"] == 2 and d["prompts_with_note"] == 1 and d["journal_nodes_with_note"] == 2
